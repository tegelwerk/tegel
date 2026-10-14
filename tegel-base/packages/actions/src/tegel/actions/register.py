"""Plan a resource's data parts and register them as externally managed Tiled children.

This module and ``staging`` are the only places in actions that build Tiled
adapters and data sources. A part's structure always comes from Tiled's own
registration adapter for its media type.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from tegel.core.contracts.resources import DataDescriptor, DataPart, StructureFamily
from tegel.core.spec import readable_storage_from_env
from tegel.core.upstreams import Upstream, UpstreamAuth, split_remote

PAYLOAD_NAME = "payload"
BYTES_MEDIA_TYPE = "application/octet-stream"


class PartError(ValueError):
    """A part that cannot be planned or registered. Staging reports it at stage ``parts``."""


@dataclass
class PartsPlan:
    """The parts to store in the envelope, and the Tiled data sources for each Tiled part."""

    parts: list[DataPart]
    payload: DataPart | None
    sources: dict[str, list[Any]] = field(default_factory=dict)


def readable_roots(environ: Mapping[str, str] | None = None) -> list[Path]:
    """Roots from ``TEGEL_READABLE_STORAGE``, or ``TEGEL_RECORDS`` when it is unset."""
    try:
        return readable_storage_from_env(environ)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def tiled_registers(media_type: str) -> bool:
    """Whether Tiled registers a single ``file:`` asset of ``media_type``."""
    from tiled.mimetypes import DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE

    if media_type == BYTES_MEDIA_TYPE or media_type.startswith("multipart/"):
        return False
    adapter_cls = DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE.get(media_type)
    return adapter_cls is not None and hasattr(adapter_cls, "from_uris")


def plan_parts(
    descriptor: DataDescriptor,
    *,
    payload_uri: str | None,
    roots: list[Path],
    check_remote: Callable[[DataPart], DataPart] | None = None,
) -> PartsPlan:
    """Set ``served_by`` on every part and build Tiled data sources for Tiled parts.

    ``check_remote(part)`` handles http(s) parts and returns the stored part.
    Raises ``PartError`` naming the part.
    """
    plan = PartsPlan(parts=[], payload=None)
    for part in descriptor.parts:
        if part.remote is not None:
            if check_remote is None:
                raise PartError(f"part {part.name}: remote parts need TEGEL_UPSTREAMS")
            plan.parts.append(check_remote(part))
            continue
        path = _within_roots(part, roots)
        if not tiled_registers(part.media_type):
            plan.parts.append(_with_size(part, path).model_copy(update={"served_by": "tegel"}))
            continue
        stored, sources = _register_plan(part, path)
        plan.parts.append(stored)
        plan.sources[part.name] = sources
    if payload_uri:
        media_type = BYTES_MEDIA_TYPE
        if descriptor.payload is not None:
            media_type = descriptor.payload.media_type
        payload = DataPart(
            name=PAYLOAD_NAME,
            family=StructureFamily.bytes,
            media_type=media_type,
            asset_uri=payload_uri,
            served_by="tegel",
        )
        plan.payload = _with_size(payload, _file_path(payload_uri))
    return plan


class RemoteChecker:
    """Check http(s) parts against ``TEGEL_UPSTREAMS`` at staging. One auth per upstream."""

    def __init__(self, upstreams: Mapping[str, Upstream], client: httpx.Client) -> None:
        self.upstreams = dict(upstreams)
        self.client = client
        self._auth: dict[str, UpstreamAuth] = {}

    def __call__(self, part: DataPart) -> DataPart:
        return check_remote_part(part, self.upstreams, self.client, self._auth)


def check_remote_part(
    part: DataPart,
    upstreams: Mapping[str, Upstream],
    client: httpx.Client,
    auth: dict[str, UpstreamAuth] | None = None,
) -> DataPart:
    """Fetch the remote node's metadata and check its family and hints (R2.3).

    Returns the part with ``served_by``, ``upstream`` and ``remote_path`` set.
    """
    try:
        upstream, remote_path = split_remote(part.asset_uri, upstreams)
    except ValueError as exc:
        raise PartError(f"part {part.name}: {exc}") from exc
    cache = {} if auth is None else auth
    credentials = cache.setdefault(upstream.origin, UpstreamAuth(upstream))
    try:
        response = client.get(part.asset_uri, auth=credentials)
    except httpx.TimeoutException as exc:
        raise PartError(f"part {part.name}: upstream {upstream.origin} timed out") from exc
    except httpx.HTTPError as exc:
        raise PartError(f"part {part.name}: upstream {upstream.origin} failed: {exc}") from exc
    if response.status_code >= 400:
        raise PartError(
            f"part {part.name}: {part.asset_uri} returned HTTP {response.status_code}"
        )
    try:
        attributes = response.json()["data"]["attributes"]
        family = str(attributes["structure_family"])
        structure = attributes.get("structure") or {}
    except (ValueError, KeyError, TypeError) as exc:
        raise PartError(f"part {part.name}: {part.asset_uri} is not Tiled node metadata") from exc
    if family != part.family.value:
        raise PartError(
            f"part {part.name}: upstream node is {family}, the part declares {part.family.value}"
        )
    updates: dict[str, Any] = {
        "served_by": part.remote,
        "upstream": upstream.origin,
        "remote_path": remote_path,
    }
    if family == StructureFamily.array.value:
        shape = [int(size) for size in structure.get("shape") or []]
        if part.shape is not None and part.shape != shape:
            raise PartError(f"part {part.name}: shape hint {part.shape} != upstream shape {shape}")
        updates["shape"] = shape
        if part.chunks is None and structure.get("chunks"):
            updates["chunks"] = [[int(size) for size in axis] for axis in structure["chunks"]]
        if part.data_type is None:
            updates["data_type"] = _numpy_dtype(structure.get("data_type"))
    elif family == StructureFamily.table.value:
        columns = [str(name) for name in structure.get("columns") or []]
        if part.columns is not None and part.columns != columns:
            raise PartError(
                f"part {part.name}: columns hint {part.columns} != upstream columns {columns}"
            )
        updates["columns"] = columns
    return part.model_copy(update=updates)


def _numpy_dtype(found: Any) -> str | None:
    if not isinstance(found, dict) or "kind" not in found or "itemsize" not in found:
        return None
    order = {"little": "<", "big": ">"}.get(str(found.get("endianness")), "|")
    return f"{order}{found['kind']}{found['itemsize']}"


def write_resource(tree: Any, *, key: str, metadata: dict[str, Any], plan: PartsPlan) -> None:
    """Create the resource container with its row, then one external child per Tiled part.

    A child that cannot be created raises ``PartError``; the container is removed first.
    """
    from tiled.structures.core import StructureFamily as TiledFamily

    if not hasattr(tree.context, "streaming_cache"):
        tree.context.streaming_cache = None

    async def _write() -> None:
        await tree.create_node(
            structure_family=TiledFamily.container,
            metadata=metadata,
            key=key,
        )
        node = await tree.lookup_adapter([key])
        for part in plan.parts:
            sources = plan.sources.get(part.name)
            if sources is None:
                continue
            try:
                await node.create_node(
                    structure_family=TiledFamily(part.family.value),
                    metadata={},
                    key=part.name,
                    data_sources=sources,
                )
            except Exception as exc:
                await node.delete(recursive=True)
                raise PartError(f"part {part.name}: Tiled registration failed: {exc}") from exc

    asyncio.run(_write())


def _register_plan(part: DataPart, path: Path) -> tuple[DataPart, list[Any]]:
    from tiled.mimetypes import DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE
    from tiled.utils import ensure_uri

    adapter_cls = DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE[part.media_type]
    parameters = {} if part.dataset is None else {"dataset": part.dataset}
    try:
        adapter = adapter_cls.from_uris(ensure_uri(str(path)), **parameters)
        family = adapter.structure_family.value
        structure = adapter.structure()
    except Exception as exc:
        raise PartError(
            f"part {part.name}: Tiled adapter for {part.media_type} failed: {exc}"
        ) from exc
    if family != part.family.value:
        raise PartError(
            f"part {part.name}: Tiled reads {part.media_type} as {family}, "
            f"the part declares {part.family.value}"
        )
    updates: dict[str, Any] = {"served_by": "tiled"}
    if family == StructureFamily.array.value:
        updates.update(_array_hints(part, structure))
    elif family == StructureFamily.table.value:
        updates.update(_table_hints(part, structure))
    stored = _with_size(part, path).model_copy(update=updates)
    try:
        sources = _data_sources(adapter, part.media_type, path, parameters)
    except Exception as exc:
        raise PartError(f"part {part.name}: Tiled data source failed: {exc}") from exc
    return stored, sources


def _array_hints(part: DataPart, structure: Any) -> dict[str, Any]:
    shape = [int(size) for size in structure.shape]
    if part.shape is not None and part.shape != shape:
        raise PartError(f"part {part.name}: shape hint {part.shape} != Tiled shape {shape}")
    updates: dict[str, Any] = {"shape": shape}
    if part.chunks is None:
        updates["chunks"] = [[int(size) for size in axis] for axis in structure.chunks]
    if part.data_type is None and hasattr(structure.data_type, "to_numpy_dtype"):
        updates["data_type"] = structure.data_type.to_numpy_dtype().str
    return updates


def _table_hints(part: DataPart, structure: Any) -> dict[str, Any]:
    columns = [str(name) for name in structure.columns]
    if part.columns is not None and part.columns != columns:
        raise PartError(f"part {part.name}: columns hint {part.columns} != Tiled columns {columns}")
    return {"columns": columns}


def _data_sources(adapter: Any, media_type: str, path: Path, parameters: dict) -> list[Any]:
    from tiled.structures.data_source import Asset, DataSource, Management
    from tiled.utils import ensure_uri

    is_directory = path.is_dir()
    if hasattr(adapter, "generate_data_sources"):
        return adapter.generate_data_sources(
            media_type,
            lambda found: None if found is None else dataclasses.asdict(found),
            path,
            is_directory,
        )
    return [
        DataSource(
            structure_family=adapter.structure_family,
            mimetype=media_type,
            structure=adapter.structure(),
            parameters=parameters,
            management=Management.external,
            assets=[
                Asset(
                    data_uri=ensure_uri(str(path)),
                    is_directory=is_directory,
                    parameter="data_uri",
                )
            ],
        )
    ]


def _within_roots(part: DataPart, roots: list[Path]) -> Path:
    path = _file_path(part.asset_uri)
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root.resolve()) for root in roots):
        listed = os.pathsep.join(str(root) for root in roots) or "(none)"
        raise PartError(f"part {part.name}: {path} is outside readable storage {listed}")
    if not path.exists():
        raise PartError(f"part {part.name}: {path} does not exist")
    return path


def _file_path(uri: str) -> Path:
    return Path(unquote(urlsplit(uri).path))


def _with_size(part: DataPart, path: Path) -> DataPart:
    if part.size is None and path.is_file():
        return part.model_copy(update={"size": path.stat().st_size})
    return part
