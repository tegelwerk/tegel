"""Stage phase: plan and read a run's inputs.

Each input URI takes one route (``InputRoute``):

- ``local``: a ``file:`` URI under readable storage, or a result on this server
- ``tiled``: a node on another Tiled server, read through its routes
- ``bytes``: a byte part (or any other http(s) URL) on another server
- ``bind``: any other scheme; tegel-register binds it into the staging catalog

``plan_input`` never reads payload bytes. ``resolve`` does, and records how long
each transfer took so the next plan from that source is better informed.
"""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from tegel.actionserver.catalogs import Catalogs
from tegel.actionserver.config import ActionServerConfig
from tegel.actionserver.runs import RunStore
from tegel.core.contracts.actions import ActionPort, InputPlan, InputRoute, PortKind

TILED_MARKER = "/api/v1/"
TILED_ROUTES = ("metadata", "array/full", "array/block", "table/full", "table/partition",
                "node/full")
BIND_SCOPE = "actions:bind"

# Called with the target origin; returns the Authorization header to send, or None.
AuthFor = Callable[[str], str | None]


class StageError(RuntimeError):
    """An input that cannot be read. The run fails with this message."""


@dataclass(frozen=True)
class Located:
    route: InputRoute
    source: str
    origin: str | None = None
    tiled_base: str | None = None  # ".../api/v1" of the Tiled server
    node_path: str | None = None  # path below the Tiled root
    local_path: Path | None = None
    resource: str | None = None  # this server's result id, for a local byte part
    part: str | None = None


class Stager:
    def __init__(
        self,
        config: ActionServerConfig,
        catalogs: Catalogs,
        store: RunStore,
        *,
        client: httpx.Client | None = None,
        bind_context: Any = None,
    ) -> None:
        self.config = config
        self.catalogs = catalogs
        self.store = store
        self.http = client or httpx.Client(timeout=config.http_timeout, follow_redirects=False)
        self._bind_context = bind_context

    # -- where ----------------------------------------------------------------------------------

    def locate(self, uri: str) -> Located:
        parts = urlsplit(uri)
        scheme = parts.scheme.lower()
        if scheme == "file":
            path = Path(unquote(parts.path))
            return Located(InputRoute.local, "file", local_path=path)
        if scheme in ("http", "https"):
            origin = f"{scheme}://{parts.netloc}"
            own = uri.startswith(self.config.public_origin + "/")
            path = parts.path
            if TILED_MARKER in path:
                prefix, _, rest = path.partition(TILED_MARKER)
                for route in TILED_ROUTES:
                    if rest.startswith(route + "/"):
                        node = rest[len(route) + 1:].strip("/")
                        return Located(
                            InputRoute.local if own else InputRoute.tiled,
                            "results" if own else origin,
                            origin=origin,
                            tiled_base=f"{origin}{prefix}{TILED_MARKER.rstrip('/')}",
                            node_path=node,
                        )
                raise StageError(f"{uri}: not a Tiled node route")
            if own:
                base = urlsplit(self.config.public_origin).path.rstrip("/")
                tail = path[len(base):].strip("/").split("/")
                if len(tail) == 5 and tail[0] == "resources" and tail[2] == "data" \
                        and tail[4] == "content":
                    return Located(InputRoute.local, "results", origin=origin,
                                   resource=tail[1], part=tail[3])
                raise StageError(f"{uri}: not a data part on this server")
            return Located(InputRoute.bytes, origin, origin=origin)
        if not scheme:
            raise StageError(f"{uri}: an input is an absolute URI")
        return Located(InputRoute.bind, scheme)

    # -- plan -----------------------------------------------------------------------------------

    def plan_input(self, port: str, uri: str, auth_for: AuthFor) -> InputPlan:
        """What staging ``uri`` will move and how long it should take. Reads no payload."""
        try:
            where = self.locate(uri)
        except StageError as exc:
            return InputPlan(port=port, uri=uri, route=InputRoute.local, notes=[str(exc)])
        notes: list[str] = []
        size: int | None = None
        moves = False
        if where.route is InputRoute.local:
            try:
                size = self._local_size(where)
            except StageError as exc:
                notes.append(str(exc))
        elif where.route is InputRoute.tiled:
            moves = True
            size, note = self._tiled_size(where, auth_for)
            if note:
                notes.append(note)
        elif where.route is InputRoute.bytes:
            moves = True
            size, note = self._bytes_size(uri, where, auth_for)
            if note:
                notes.append(note)
        else:
            size, moves, bind_notes = self._bind_plan(uri)
            notes.extend(bind_notes)
        seconds, measured = None, False
        if moves and size is not None:
            rate = self.store.throughput(where.source)
            measured = rate is not None
            seconds = size / (rate or self.config.default_throughput)
        elif moves:
            notes.append("size unknown: transfer time cannot be estimated")
        return InputPlan(
            port=port, uri=uri, route=where.route, source=where.source, size_bytes=size,
            moves_bytes=moves, seconds=seconds, throughput_measured=measured, notes=notes,
        )

    def _local_size(self, where: Located) -> int | None:
        if where.local_path is not None:
            path = self._readable(where.local_path)
            return path.stat().st_size if path.is_file() else None
        if where.resource is not None:
            return self._result_part_path(where).stat().st_size
        return None

    def _tiled_size(self, where: Located, auth_for: AuthFor) -> tuple[int | None, str | None]:
        url = f"{where.tiled_base}/metadata/{where.node_path}"
        try:
            response = self.http.get(url, headers=_headers(auth_for(where.origin or "")))
        except httpx.HTTPError as exc:
            return None, f"metadata unavailable: {type(exc).__name__}"
        if response.status_code != 200:
            return None, f"metadata returned HTTP {response.status_code}"
        try:
            attributes = response.json()["data"]["attributes"]
        except (ValueError, KeyError, TypeError):
            return None, "metadata is not a Tiled node"
        family = attributes.get("structure_family")
        structure = attributes.get("structure") or {}
        if family == "array":
            shape = structure.get("shape") or []
            itemsize = (structure.get("data_type") or {}).get("itemsize")
            if isinstance(itemsize, int) and all(isinstance(n, int) for n in shape):
                return int(math.prod(shape)) * itemsize, None
        return None, f"size unknown for a {family} node"

    def _bytes_size(
        self, uri: str, where: Located, auth_for: AuthFor
    ) -> tuple[int | None, str | None]:
        headers = _headers(auth_for(where.origin or ""))
        try:
            response = self.http.head(uri, headers=headers)
            if response.status_code == 200 and response.headers.get("content-length"):
                return int(response.headers["content-length"]), None
            response = self.http.get(uri, headers={**headers, "Range": "bytes=0-0"})
        except httpx.HTTPError as exc:
            return None, f"size unavailable: {type(exc).__name__}"
        total = response.headers.get("content-range", "").rpartition("/")[2]
        if response.status_code == 206 and total.isdigit():
            return int(total), None
        return None, f"size unavailable: HTTP {response.status_code}"

    def _bind_plan(self, uri: str) -> tuple[int | None, bool, list[str]]:
        try:
            from tegel.register import bind

            planned = bind.plan(uri, self.catalogs.staging, key=_bind_key(uri),
                                context=self._bind())
        except Exception as exc:  # the run would fail the same way; the plan says why
            return None, True, [f"bind not possible: {exc}"]
        return planned.size_bytes, planned.moves_bytes, list(planned.notes)

    def _bind(self) -> Any:
        if self._bind_context is None:
            from tegel.register.context import BindContext

            self._bind_context = BindContext.default()
        return self._bind_context

    # -- read -----------------------------------------------------------------------------------

    def resolve(self, port: ActionPort, value: str | list[str], auth_for: AuthFor,
                work_dir: Path) -> Any:
        """The value ``port`` receives: an array, a table, parsed JSON, or a local path."""
        if port.kind is PortKind.selection:
            assert isinstance(value, list)
            return [self._one(port, uri, auth_for, work_dir, index) for index, uri in
                    enumerate(value)]
        assert isinstance(value, str)
        return self._one(port, value, auth_for, work_dir, None)

    def _one(self, port: ActionPort, uri: str, auth_for: AuthFor, work_dir: Path,
             index: int | None) -> Any:
        where = self.locate(uri)
        label = port.name if index is None else f"{port.name}[{index}]"
        if where.route is InputRoute.local and where.local_path is not None:
            return _from_file(port.kind, self._readable(where.local_path))
        if where.route is InputRoute.local and where.resource is not None:
            return _from_file(port.kind, self._result_part_path(where))
        if where.route is InputRoute.local:
            node = self._own_node(where.node_path or "")
            return _from_node(port.kind, node, work_dir, label)
        if where.route is InputRoute.tiled:
            return self._read_tiled(port, where, auth_for, work_dir, label)
        if where.route is InputRoute.bytes:
            path = self._download(uri, where, auth_for, work_dir, label)
            return _from_file(port.kind, path)
        return self._read_bound(port, uri, work_dir, label)

    def _readable(self, path: Path) -> Path:
        resolved = path.resolve()
        roots = [root.resolve() for root in self.config.readable_storage]
        if not any(resolved.is_relative_to(root) for root in roots):
            listed = ", ".join(str(r) for r in roots) or "(none)"
            raise StageError(f"{path} is not under readable storage ({listed})")
        if not resolved.exists():
            raise StageError(f"{path} does not exist")
        return resolved

    def _result_part_path(self, where: Located) -> Path:
        node = self._own_node(where.resource or "")
        row = dict(node.metadata)
        envelope = row.get("tegel") or {}
        candidates = list(envelope.get("parts") or [])
        if envelope.get("payload"):
            candidates.append(envelope["payload"])
        for part in candidates:
            if part.get("name") == where.part:
                return Path(unquote(urlsplit(part["asset_uri"]).path))
        raise StageError(f"result {where.resource} has no part {where.part!r}")

    def _own_node(self, path: str) -> Any:
        node = self.catalogs.results
        try:
            for segment in [s for s in path.split("/") if s]:
                node = node[segment]
        except KeyError as exc:
            raise StageError(f"{path} is not a result on this server") from exc
        return node

    def _read_tiled(self, port: ActionPort, where: Located, auth_for: AuthFor, work_dir: Path,
                    label: str) -> Any:
        """Read a node on another Tiled server through its HTTP routes, with the caller's header.

        Arrays come as raw bytes (``application/octet-stream``) shaped by the node's
        structure; tables as an Arrow file. JSON ports get the node's metadata.
        """
        headers = _headers(auth_for(where.origin or ""))
        base, path = where.tiled_base, where.node_path
        started = time.monotonic()

        def get(route: str, **params: str) -> httpx.Response:
            try:
                response = self.http.get(f"{base}/{route}/{path}", headers=headers,
                                         params=params or None)
            except httpx.HTTPError as exc:
                raise StageError(f"{label}: reading {path} on {where.origin} failed: "
                                 f"{type(exc).__name__}") from exc
            if response.status_code != 200:
                raise StageError(f"{label}: reading {path} on {where.origin} returned HTTP "
                                 f"{response.status_code}")
            return response

        try:
            attributes = get("metadata").json()["data"]["attributes"]
            family = attributes["structure_family"]
            structure = attributes.get("structure") or {}
        except (ValueError, KeyError, TypeError) as exc:
            raise StageError(f"{label}: {path} on {where.origin} is not a Tiled node") from exc
        if port.kind is PortKind.json:
            return dict(attributes.get("metadata") or {})
        if family == "array" and port.kind in (PortKind.array, PortKind.selection):
            import numpy as np

            raw = get("array/full", format="application/octet-stream").content
            dtype = _numpy_dtype(structure.get("data_type"))
            value = np.frombuffer(raw, dtype=dtype).reshape(structure.get("shape") or [-1]).copy()
        elif family == "table" and port.kind in (PortKind.table, PortKind.selection):
            import io

            import pyarrow

            raw = get("table/full", format="application/vnd.apache.arrow.file").content
            value = pyarrow.ipc.open_file(io.BytesIO(raw)).read_all().to_pandas()
        else:
            raise StageError(f"{label}: a {family} node cannot feed a {port.kind.value} port")
        self.store.record_transfer(where.source, len(raw), time.monotonic() - started)
        return value

    def _download(self, uri: str, where: Located, auth_for: AuthFor, work_dir: Path,
                  label: str) -> Path:
        name = Path(unquote(urlsplit(uri).path)).name or "download"
        if name == "content":  # a Tegel byte part: .../data/<part>/content
            name = Path(unquote(urlsplit(uri).path)).parent.name
        target = work_dir / "inputs" / label / name
        target.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            with self.http.stream("GET", uri, headers=_headers(auth_for(where.origin or ""))) \
                    as response:
                if response.status_code != 200:
                    raise StageError(f"{label}: {uri} returned HTTP {response.status_code}")
                media = response.headers.get("content-type", "").split(";")[0].strip()
                with target.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            raise StageError(f"{label}: fetching {uri} failed: {type(exc).__name__}") from exc
        if not target.suffix and media:
            guessed = mimetypes.guess_extension(media)
            if guessed:
                target = target.rename(target.with_suffix(guessed))
        self.store.record_transfer(where.source, target.stat().st_size,
                                   time.monotonic() - started)
        return target

    def _read_bound(self, port: ActionPort, uri: str, work_dir: Path, label: str) -> Any:
        from tegel.register import bind

        started = time.monotonic()
        try:
            with self.catalogs.staging_lock:
                binding = bind.register(uri, self.catalogs.staging, key=_bind_key(uri),
                                        on_conflict="return", context=self._bind())
        except Exception as exc:
            raise StageError(f"{label}: binding {uri} failed: {exc}") from exc
        if binding.acquired_path:
            path = Path(binding.acquired_path)
            size = path.stat().st_size if path.is_file() else 0
            self.store.record_transfer(urlsplit(uri).scheme, size, time.monotonic() - started)
            if port.kind in (PortKind.file, PortKind.structure, PortKind.trajectory,
                             PortKind.json, PortKind.selection):
                return _from_file(port.kind, path)
        return _from_node(port.kind, binding.node, work_dir, label)


def _headers(authorization: str | None) -> dict[str, str]:
    return {"Authorization": authorization} if authorization else {}


def _numpy_dtype(found: Any) -> str:
    """A numpy dtype string from Tiled's ``data_type`` structure."""
    if not isinstance(found, dict) or "kind" not in found or "itemsize" not in found:
        raise StageError("the array node has no usable data_type")
    order = {"little": "<", "big": ">"}.get(str(found.get("endianness")), "|")
    return f"{order}{found['kind']}{found['itemsize']}"


def _bind_key(uri: str) -> str:
    """One staging node per bound URI, reused by later runs."""
    return "b" + hashlib.sha256(uri.encode()).hexdigest()[:24]


def _from_node(kind: PortKind, node: Any, work_dir: Path, label: str) -> Any:
    family = getattr(node, "structure_family", None)
    family = getattr(family, "value", family)
    if kind in (PortKind.array,) and family == "array":
        return node.read()
    if kind is PortKind.table and family == "table":
        return node.read()
    if kind in (PortKind.selection,) and family in ("array", "table"):
        return node.read()
    if kind is PortKind.json:
        return dict(node.metadata)
    raise StageError(f"{label}: a {family} node cannot feed a {kind.value} port")


def _from_file(kind: PortKind, path: Path) -> Any:
    suffix = path.suffix.lower()
    if kind is PortKind.array:
        if suffix == ".npy":
            import numpy as np

            return np.load(path, allow_pickle=False)
        raise StageError(f"{path.name}: an array input from a file must be .npy")
    if kind is PortKind.table:
        import pandas as pd

        if suffix == ".parquet":
            return pd.read_parquet(path)
        if suffix in (".csv", ".txt"):
            return pd.read_csv(path)
        raise StageError(f"{path.name}: a table input from a file must be .parquet or .csv")
    if kind in (PortKind.json, PortKind.quantity):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StageError(f"{path.name}: not JSON") from exc
    return path


def copy_into(path: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / path.name
    shutil.copy2(path, target)
    return target
