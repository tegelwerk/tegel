"""Entry-point discovery for binders and sources.

Conflicts are recorded at enumeration and raised on the first ``get`` of that
protocol, not at bootstrap. Built-in binders are lazy proxies: importing this
module does not import ``tiled``, ``globus_sdk``, or a binder implementation.
"""
from __future__ import annotations

from importlib import import_module
from importlib.metadata import entry_points
from typing import Any

from tegel.register.binders._registry import BinderRegistry, LazyBinderProxy, get_global_registry
from tegel.register.errors import PluginConflictError, PluginError

_ENUMERATED: dict[str, Any] = {}
_SOURCES: dict[str, str] = {}
_CONFLICTS: dict[str, tuple[str, str]] = {}
_LOADED = False
_BOOTSTRAPPED = False

_BUILTINS: tuple[tuple[str, str, str], ...] = (
    ("file", "tegel.register.binders.file", "FileBinder"),
    ("tiled", "tegel.register.binders.tiled", "TiledBinder"),
    ("globus", "tegel.register.binders.globus", "GlobusBinder"),
    ("zenodo", "tegel.register.binders.zenodo", "ZenodoBinder"),
)


def _entry_points(group: str):
    found = entry_points()
    if hasattr(found, "select"):
        return list(found.select(group=group))
    return list(found.get(group, []))  # type: ignore[arg-type]


def clear_plugin_conflict(protocol: str) -> None:
    """Clear a deferred entry-point conflict after explicit registration."""
    _CONFLICTS.pop(protocol.lower(), None)


def get_plugin_conflicts() -> dict[str, tuple[str, str]]:
    return dict(_CONFLICTS)


def reset_plugin_state() -> None:
    """Drop enumerated entry points. Tests use this between cases."""
    global _LOADED, _BOOTSTRAPPED
    _ENUMERATED.clear()
    _SOURCES.clear()
    _CONFLICTS.clear()
    _LOADED = False
    _BOOTSTRAPPED = False


def enumerate_binder_entry_points() -> dict[str, Any]:
    global _LOADED
    found: dict[str, Any] = {}
    sources: dict[str, str] = {}
    _CONFLICTS.clear()
    for entry_point in _entry_points("tegel.binders"):
        protocol = entry_point.name.lower()
        dist = getattr(entry_point, "dist", None)
        source = dist.name if dist is not None else entry_point.value
        if protocol in found:
            _CONFLICTS[protocol] = (sources[protocol], source)
            continue
        found[protocol] = entry_point
        sources[protocol] = source
    _ENUMERATED.clear()
    _ENUMERATED.update(found)
    _SOURCES.clear()
    _SOURCES.update(sources)
    _LOADED = True
    return dict(found)


def _builtin_loader(module: str, attribute: str):
    def load() -> Any:
        imported = import_module(module)
        obj = getattr(imported, attribute)
        return obj() if isinstance(obj, type) else obj

    return load


def install_builtins(registry: BinderRegistry) -> None:
    for protocol, module, attribute in _BUILTINS:
        if protocol in registry.protocols():
            continue
        proxy = LazyBinderProxy(protocol, _builtin_loader(module, attribute))
        registry.register(protocol, proxy, source="tegel-register")


def install_entry_points(registry: BinderRegistry | None = None) -> None:
    registry = registry or get_global_registry()
    enumerated = enumerate_binder_entry_points()
    for protocol in enumerated:
        if protocol in _CONFLICTS or protocol in registry.protocols():
            continue
        registry.register(
            protocol,
            LazyBinderProxy(protocol, _entry_point_loader(protocol)),
            source=_SOURCES.get(protocol, "entry_point"),
        )


def _entry_point_loader(protocol: str):
    def load() -> Any:
        if not _LOADED:
            enumerate_binder_entry_points()
        if protocol in _CONFLICTS:
            left, right = _CONFLICTS[protocol]
            raise PluginConflictError(
                f"protocol {protocol!r} registered by both {left!r} and {right!r}"
            )
        entry_point = _ENUMERATED[protocol]
        try:
            obj = entry_point.load()
        except ImportError as exc:
            raise PluginError(f"failed to import binder for protocol {protocol!r}") from exc
        return obj() if isinstance(obj, type) else obj

    return load


def bootstrap(registry: BinderRegistry | None = None) -> None:
    """Register built-ins and entry points without importing binder modules."""
    global _BOOTSTRAPPED
    registry = registry or get_global_registry()
    if registry is get_global_registry():
        if _BOOTSTRAPPED:
            return
        _BOOTSTRAPPED = True
    install_builtins(registry)
    install_entry_points(registry)


def load_source_entries() -> dict[str, dict[str, Any]]:
    """Merge ``tegel.register.sources`` entry points. The caller overlays the site file."""
    merged: dict[str, dict[str, Any]] = {}
    owners: dict[str, str] = {}
    for entry_point in _entry_points("tegel.binder_sources"):
        dist = getattr(entry_point, "dist", None)
        source = dist.name if dist is not None else entry_point.value
        try:
            factory = entry_point.load()
            payload = factory() if callable(factory) else factory
        except Exception as exc:
            raise PluginError(f"failed to load sources from {source!r}") from exc
        if not isinstance(payload, dict):
            raise PluginError(f"sources entry point {source!r} did not return a mapping")
        for scheme, body in payload.items():
            if scheme in owners:
                raise PluginConflictError(
                    f"scheme {scheme!r} registered by both {owners[scheme]!r} and {source!r}"
                )
            if not isinstance(body, dict):
                raise PluginError(f"sources entry point {source!r} scheme {scheme!r} is not a mapping")
            owners[scheme] = source
            merged[scheme] = body
    return merged
