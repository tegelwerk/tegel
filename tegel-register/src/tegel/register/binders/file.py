"""Reference bytes the target server can already read, or copy them into landing storage."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from tegel.register._uriparse import split_uri
from tegel.register.bind import Acquisition, BinderPlan, Mode, NodeSpec
from tegel.register.context import BindContext
from tegel.register.errors import BindError, DescribeError, InvalidURIError, NotReadableError
from tegel.register.site import Source

_MODES = (Mode.EXISTING, Mode.REFERENCE, Mode.PROXY, Mode.ACQUIRE)


class FileBinder:
    """Bind a local ``file:`` URI by referencing readable storage."""

    api_version = 2
    protocol = "file"
    opaque_payload = False

    def validate_source(self, source: Source) -> None:
        return None

    def feasible_modes(
        self, uri: str, source: Source | None, into: Any, ctx: BindContext
    ) -> list[tuple[Mode, str | None]]:
        local, _recursive = _resolve(uri)
        server = ctx.site.to_server_path(local) if ctx.site is not None else None
        roots = _roots(ctx)
        reference = None if server is not None else f"not under any readable entry: {roots}"
        acquire = None if ctx.site is not None and ctx.site.landing is not None else "landing is not configured"
        reasons = {
            Mode.EXISTING: "file URIs are not nodes on the target server",
            Mode.REFERENCE: reference,
            Mode.PROXY: "file URIs are not an upstream Tiled node",
            Mode.ACQUIRE: acquire,
        }
        return [(mode, reasons[mode]) for mode in _MODES]

    def plan(
        self,
        uri: str,
        source: Source | None,
        into: Any,
        ctx: BindContext,
        *,
        mode: Mode,
        key: str | None,
    ) -> BinderPlan:
        local, recursive = _resolve(uri)
        if mode is Mode.ACQUIRE:
            name = _safe_key(key, local.name, allow_suffixes=True)
            landing = ctx.site.landing_path("file", uri, Path(name).name)
            size = _local_size(local)
            return BinderPlan(
                acquisition=Acquisition(
                    protocol="file",
                    resolved_uri=uri,
                    landing_local=str(landing),
                    recursive=recursive or local.is_dir(),
                    expected_size=size,
                ),
                size_bytes=size,
            )
        if mode is not Mode.REFERENCE:
            raise BindError(f"file binder cannot plan mode {mode.value}")
        server = ctx.site.to_server_path(local)
        if server is None:
            raise NotReadableError(f"not under any readable entry: {_roots(ctx)}")
        node, notes = _describe_tree(local, server, ctx, key=key, recursive=recursive, top=True)
        if node is None:
            raise DescribeError(f"Tiled cannot describe {local}")
        return BinderPlan(node=node, notes=tuple(notes), size_bytes=_local_size(local))

    def acquire(self, acquisition: Acquisition, ctx: BindContext, *, timeout: float | None) -> Path:
        del ctx, timeout
        source, _recursive = _resolve(acquisition.resolved_uri)
        return _land_copy(source, Path(acquisition.landing_local), directory=acquisition.recursive)


def _land_copy(source: Path, dest: Path, *, directory: bool) -> Path:
    """Copy ``source`` onto ``dest`` via a sibling temp path, then rename."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".partial")
    if tmp.exists():
        shutil.rmtree(tmp) if tmp.is_dir() else tmp.unlink()
    try:
        if directory:
            shutil.copytree(source, tmp)
        else:
            shutil.copy2(source, tmp)
        os.replace(tmp, dest)
    except Exception:
        if tmp.is_dir():
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            tmp.unlink(missing_ok=True)
        raise
    return dest


def _describe_tree(local: Path, server: Path, ctx: BindContext, *, key: str | None, recursive: bool, top: bool):
    from tegel.register.tiled.describe import describe_local

    description = describe_local(
        local,
        server_uri=Path(server).as_uri(),
        is_directory=local.is_dir(),
        mimetypes_by_file_ext=ctx.site.mimetypes_by_file_ext,
        adapters_by_mimetype=ctx.site.adapters_by_mimetype,
    )
    if description is not None:
        node_key = _safe_key(key, local.name)
        return (
            NodeSpec(
                key=node_key,
                structure_family=description.structure_family,
                data_sources=description.data_sources,
                metadata=description.metadata,
                specs=description.specs,
            ),
            [],
        )
    if not local.is_dir():
        return None, [f"skip {local.name}: undescribable"]
    if top and not recursive:
        raise BindError("directory needs ?recursive")
    notes: list[str] = []
    children: list[NodeSpec] = []
    for child in sorted(local.iterdir(), key=lambda item: item.name):
        if child.name.startswith("."):
            continue
        child_server = ctx.site.to_server_path(child)
        if child_server is None:
            notes.append(f"skip {child.name}: not under any readable entry: {_roots(ctx)}")
            continue
        child_node, child_notes = _describe_tree(
            child, child_server, ctx, key=None, recursive=True, top=False
        )
        notes.extend(child_notes)
        if child_node is None and not child_notes:
            notes.append(f"skip {child.name}: undescribable")
        elif child_node is not None:
            children.append(child_node)
    node_key = _safe_key(key, local.name)
    return (
        NodeSpec(
            key=node_key,
            structure_family="container",
            data_sources=(),
            metadata={},
            specs=(),
            children=tuple(children),
        ),
        notes,
    )


def _safe_key(explicit: str | None, name: str, *, allow_suffixes: bool = False) -> str:
    if explicit:
        key = explicit
    elif allow_suffixes:
        key = name
    else:
        from tegel.register.tiled._compat import strip_suffixes

        key = strip_suffixes(name)
    if not key or key in {".", ".."} or "/" in key or "\\" in key:
        raise BindError(f"derived key {key!r} is not a valid Tiled key; pass key=")
    return key


def _roots(ctx: BindContext) -> str:
    if ctx.site is None or not ctx.site.readable:
        return "(none)"
    return ", ".join(str(entry.local) for entry in ctx.site.readable)


def _resolve(uri: str) -> tuple[Path, bool]:
    path, recursive = _uri_to_path(uri)
    return path.resolve(strict=True), recursive


def _uri_to_path(uri: str) -> tuple[Path, bool]:
    """Map a file: URI to a local path (RFC 8089). ``?recursive`` is the only query."""
    parts = split_uri(uri)
    if parts.scheme != "file":
        raise InvalidURIError(f"FileBinder received a URI with scheme {parts.scheme!r}: {uri!r}")
    parsed = urlparse(uri.split("#", 1)[0])
    recursive = False
    if parsed.query:
        if parsed.query != "recursive":
            raise InvalidURIError(f"file: URIs define no query component (RFC 8089); refusing {uri!r}")
        recursive = True
    path_part = parsed.path
    if os.name == "posix" and ("%2F" in path_part.upper() or "%2f" in path_part):
        raise InvalidURIError(f"file: URI percent-encodes a path separator (RFC 3986 §2.2): {uri!r}")
    host = parsed.netloc
    if host and host.lower() != "localhost":
        if os.name != "nt":
            raise InvalidURIError(
                f"file: URI names the non-local host {host!r}; only an empty "
                f"authority or 'localhost' is supported on this platform "
                f"(RFC 8089 §2, Appendix E.3.2)"
            )
        path = "//" + host + url2pathname(parsed.path)
    else:
        path = url2pathname(parsed.path)
    result = Path(path)
    if not result.is_absolute():
        raise InvalidURIError(f"file: URI does not name an absolute path: {uri!r}")
    return result, recursive


def _local_size(path: Path) -> int | None:
    """Total bytes under ``path``; ``None`` when it cannot be read."""
    try:
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return None
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        return None
