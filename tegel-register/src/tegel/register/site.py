"""Site configuration: readable storage, landing, and sources.

Lookup order is an explicit path, then ``TEGEL_REGISTER_SITE``, then
``~/.config/tegel-register/site.yaml``. Entry-point sources are merged under the
file (the file wins per scheme). An explicit path or environment variable is
used whole and is not merged with the user file.

Relative ``readable`` and ``landing.local`` paths in a site file are resolved
against that file's directory. Symlinks are kept as written, because Tiled
compares the server root as text. ``from_mapping`` still requires absolute
paths.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from tegel.register._uriparse import split_uri
from tegel.register.errors import SiteConfigError

SITE_ENV = "TEGEL_REGISTER_SITE"
_TOP_LEVEL = frozenset({"version", "target", "readable", "landing", "sources", "tiled"})


def select_secret_id(
    path: str, *, default: str | None, by_prefix: Mapping[str, str]
) -> str | None:
    """Pick a secret id for one resource path.

    ``by_prefix`` maps a path prefix to a secret id. The longest prefix that
    is the path, or a parent of it, wins. Otherwise ``default`` is used, which
    may be ``None`` when this resource has no credential.
    """
    normalized = path.strip("/")
    best: tuple[int, str] | None = None
    for raw_prefix, secret_id in by_prefix.items():
        prefix = raw_prefix.strip("/")
        if not prefix:
            continue
        if normalized == prefix or normalized.startswith(prefix + "/"):
            if best is None or len(prefix) > best[0]:
                best = (len(prefix), secret_id)
    if best is not None:
        return best[1]
    return default


def _user_site() -> Path:
    return Path.home() / ".config" / "tegel-register" / "site.yaml"


def _require_mapping(value: object, key: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SiteConfigError(f"{key} must be a mapping")
    return value


def _absolute(value: object, key: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise SiteConfigError(f"{key} must be an absolute path")
    path = PurePosixPath(value)
    if not path.is_absolute():
        raise SiteConfigError(f"{key} must be an absolute path")
    return path


def _nests(left: PurePosixPath, right: PurePosixPath) -> bool:
    if left == right:
        return True
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class PathMap:
    local: PurePosixPath
    server: PurePosixPath


@dataclass(frozen=True)
class Landing:
    local: PurePosixPath
    layout: str
    globus_collection: str | None
    globus_path: PurePosixPath | None


@dataclass(frozen=True)
class Source:
    scheme: str
    protocol: str
    params: Mapping[str, Any]


@dataclass(frozen=True)
class Site:
    readable: tuple[PathMap, ...] = ()
    landing: Landing | None = None
    sources: Mapping[str, Source] = field(default_factory=dict)
    target_base_uri: str | None = None
    target_secret_id: str | None = None
    mimetypes_by_file_ext: Mapping[str, str] = field(default_factory=dict)
    adapters_by_mimetype: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | None = None) -> Site:
        """Load a site file, resolve relative storage paths, and merge entry-point sources underneath it."""
        explicit = path is not None or bool(os.environ.get(SITE_ENV))
        if path is not None:
            file = Path(path)
        elif os.environ.get(SITE_ENV):
            file = Path(os.environ[SITE_ENV])
        else:
            file = _user_site()
        data: dict[str, Any] = {}
        if file.is_file():
            data = _resolve_relative_paths(_read_yaml(file), Path(file).absolute().parent)
        elif explicit:
            raise SiteConfigError(f"site file not found: {file}")
        data = _merge_entry_point_sources(data)
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: Mapping) -> Site:
        """Build a site from a mapping. Used by tests and ``load``."""
        if data is None:
            data = {}
        if not isinstance(data, Mapping):
            raise SiteConfigError("site must be a mapping")
        unknown = [key for key in data if key not in _TOP_LEVEL]
        if unknown:
            raise SiteConfigError(f"unknown site key: {unknown[0]}")
        version = data.get("version", 1)
        if version != 1:
            raise SiteConfigError(f"version must be 1, got {version!r}")
        target_base, target_secret = _target(data.get("target"))
        readable = _readable(data.get("readable"))
        landing = _landing(data.get("landing"), readable)
        sources = _sources(data.get("sources"))
        mimetypes, adapters = _tiled_overrides(data.get("tiled"))
        return cls(
            readable=readable,
            landing=landing,
            sources=sources,
            target_base_uri=target_base,
            target_secret_id=target_secret,
            mimetypes_by_file_ext=mimetypes,
            adapters_by_mimetype=adapters,
        )

    def source_for(self, uri: str) -> Source | None:
        """Return the source for ``uri``'s scheme, or None."""
        try:
            scheme = split_uri(uri).scheme
        except ValueError as exc:
            raise SiteConfigError(str(exc)) from exc
        return self.sources.get(scheme)

    def to_server_path(self, local: Path) -> PurePosixPath | None:
        """Map a local file onto the server path of the longest containing root.

        Both the file and each ``readable`` local root are resolved first.
        The server root is used verbatim, because Tiled compares paths as text.
        Returns None when no root contains the resolved file.
        """
        resolved = Path(local).resolve(strict=True)
        best: tuple[int, PathMap, Path] | None = None
        for entry in self.readable:
            root = Path(entry.local).resolve(strict=True)
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            if best is None or len(root.parts) > best[0]:
                best = (len(root.parts), entry, relative)
        if best is None:
            return None
        _length, entry, relative = best
        return PurePosixPath(entry.server) / PurePosixPath(relative.as_posix())

    def landing_path(self, protocol: str, resolved_uri: str, name: str) -> Path:
        """Absolute local path where an acquisition for ``resolved_uri`` lands."""
        if self.landing is None:
            raise SiteConfigError("landing is not configured")
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise SiteConfigError(f"landing name is not a single path segment: {name!r}")
        digest = hashlib.sha256(resolved_uri.encode("utf-8")).hexdigest()
        relative = self.landing.layout.format(
            protocol=protocol, sha12=digest[:12], sha64=digest, name=name
        )
        relative_path = PurePosixPath(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise SiteConfigError(f"landing layout escaped the landing directory: {relative}")
        return Path(self.landing.local) / Path(relative_path)


def _resolve_relative_paths(data: dict[str, Any], base: Path) -> dict[str, Any]:
    """Make readable and landing paths absolute without following symlinks."""
    resolved = dict(data)
    readable = resolved.get("readable")
    if isinstance(readable, list):
        entries = []
        for item in readable:
            if isinstance(item, Mapping):
                item = dict(item)
                for key in ("local", "server"):
                    if isinstance(item.get(key), str):
                        item[key] = _against_site_file(item[key], base)
            entries.append(item)
        resolved["readable"] = entries
    landing = resolved.get("landing")
    if isinstance(landing, Mapping) and isinstance(landing.get("local"), str):
        landing = dict(landing)
        landing["local"] = _against_site_file(landing["local"], base)
        resolved["landing"] = landing
    return resolved


def _against_site_file(value: str, base: Path) -> str:
    if PurePosixPath(value).is_absolute():
        return value
    return os.path.normpath(os.path.join(str(base), value))


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise SiteConfigError("reading a site file requires pyyaml") from exc
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise SiteConfigError(f"site file is not a mapping: {path}")
    return loaded


def _merge_entry_point_sources(data: dict[str, Any]) -> dict[str, Any]:
    from tegel.register.binders._plugins import load_source_entries

    discovered = load_source_entries()
    if not discovered:
        return data
    merged = dict(data)
    sources = dict(discovered)
    file_sources = merged.get("sources") or {}
    if not isinstance(file_sources, Mapping):
        raise SiteConfigError("sources must be a mapping")
    sources.update(file_sources)
    merged["sources"] = sources
    if "version" not in merged:
        merged["version"] = 1
    return merged


def _target(raw: object) -> tuple[str | None, str | None]:
    if raw is None:
        return None, None
    mapping = _require_mapping(raw, "target")
    base = mapping.get("base_uri")
    secret = mapping.get("secret_id")
    if base is not None and (not isinstance(base, str) or not base):
        raise SiteConfigError("target.base_uri must be a URI")
    if secret is not None and (not isinstance(secret, str) or not secret):
        raise SiteConfigError("target.secret_id must be a secret id")
    if isinstance(base, str):
        base = base.rstrip("/")
    return base, secret if isinstance(secret, str) else None


def _readable(raw: object) -> tuple[PathMap, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise SiteConfigError("readable must be a list")
    entries: list[PathMap] = []
    for index, item in enumerate(raw):
        mapping = _require_mapping(item, f"readable[{index}]")
        local = _absolute(mapping.get("local"), f"readable[{index}].local")
        server = _absolute(mapping.get("server"), f"readable[{index}].server")
        entries.append(PathMap(local=local, server=server))
    _reject_nesting(entries, "local")
    _reject_nesting(entries, "server")
    return tuple(entries)


def _reject_nesting(entries: list[PathMap], attr: str) -> None:
    for index, entry in enumerate(entries):
        path = getattr(entry, attr)
        for other_index, other in enumerate(entries):
            if other_index <= index:
                continue
            if _nests(path, getattr(other, attr)):
                raise SiteConfigError(
                    f"readable[{index}].{attr} nests with readable[{other_index}].{attr}"
                )


def _landing(raw: object, readable: tuple[PathMap, ...]) -> Landing | None:
    if raw is None:
        return None
    mapping = _require_mapping(raw, "landing")
    local = _absolute(mapping.get("local"), "landing.local")
    layout = mapping.get("layout")
    if not isinstance(layout, str) or "{name}" not in layout:
        raise SiteConfigError("landing.layout must contain {name}")
    if "{sha12}" not in layout and "{sha64}" not in layout:
        raise SiteConfigError("landing.layout must contain {sha12} or {sha64}")
    if not any(_is_inside(local, entry.local) for entry in readable):
        raise SiteConfigError("landing.local must be inside a readable entry")
    globus = mapping.get("globus")
    collection: str | None = None
    globus_path: PurePosixPath | None = None
    if globus is not None:
        globus_map = _require_mapping(globus, "landing.globus")
        raw_collection = globus_map.get("collection")
        if not isinstance(raw_collection, str) or not raw_collection:
            raise SiteConfigError("landing.globus.collection must be a collection id")
        collection = raw_collection
        globus_path = _absolute(globus_map.get("path"), "landing.globus.path")
    return Landing(
        local=local, layout=layout, globus_collection=collection, globus_path=globus_path
    )


def _is_inside(child: PurePosixPath, parent: PurePosixPath) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _sources(raw: object) -> dict[str, Source]:
    if raw is None:
        return {}
    mapping = _require_mapping(raw, "sources")
    sources: dict[str, Source] = {}
    for scheme, value in mapping.items():
        if not isinstance(scheme, str) or not scheme:
            raise SiteConfigError("sources keys must be scheme names")
        body = _require_mapping(value, f"sources.{scheme}")
        protocol = body.get("protocol")
        if not isinstance(protocol, str) or not protocol:
            raise SiteConfigError(f"sources.{scheme}.protocol is required")
        params = {key: item for key, item in body.items() if key != "protocol"}
        sources[scheme] = Source(scheme=scheme, protocol=protocol, params=params)
    return sources


def _tiled_overrides(raw: object) -> tuple[dict[str, str], dict[str, str]]:
    if raw is None:
        return {}, {}
    mapping = _require_mapping(raw, "tiled")
    return (
        _string_map(mapping.get("mimetypes_by_file_ext"), "tiled.mimetypes_by_file_ext"),
        _string_map(mapping.get("adapters_by_mimetype"), "tiled.adapters_by_mimetype"),
    )


def _string_map(raw: object, key: str) -> dict[str, str]:
    if raw is None:
        return {}
    mapping = _require_mapping(raw, key)
    result: dict[str, str] = {}
    for name, value in mapping.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise SiteConfigError(f"{key} must map strings to strings")
        result[name] = value
    return result
