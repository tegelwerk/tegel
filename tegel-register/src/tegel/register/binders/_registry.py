"""Protocol → binder registry.

Registration does not touch ``opaque_payload``. That attribute is read only
when a binder is first loaded, so a lazy proxy stays unloaded.
"""
from __future__ import annotations

from typing import Any, Callable

from tegel.register.errors import PluginConflictError, PluginError, PluginVersionError, UnknownSchemeError
from tegel.register.redaction import register_opaque_scheme

BinderFactory = Callable[[], Any]
SUPPORTED_API_VERSION = 2


class LazyBinderProxy:
    """Defers importing a binder until ``load``."""

    def __init__(self, protocol: str, loader: Callable[[], Any]) -> None:
        self.protocol = protocol
        self._loader = loader
        self._binder: Any | None = None

    def load(self) -> Any:
        if self._binder is None:
            self._binder = check_binder(self._loader(), self.protocol)
        return self._binder


def check_binder(binder: Any, protocol: str) -> Any:
    """Refuse anything other than binder API version 2."""
    api_version = getattr(binder, "api_version", None)
    if api_version != SUPPORTED_API_VERSION:
        raise PluginVersionError(
            f"binder for {protocol!r} declares api_version={api_version!r}; "
            f"supported={SUPPORTED_API_VERSION}"
        )
    if getattr(binder, "opaque_payload", False):
        register_opaque_scheme(getattr(binder, "protocol", protocol))
    return binder


class BinderRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Any] = {}
        self._sources: dict[str, str] = {}
        self._overrides: dict[str, Any] = {}

    def register(
        self,
        protocol: str,
        binder: Any,
        *,
        source: str = "explicit",
        allow_override: bool = False,
    ) -> None:
        key = protocol.lower()
        if key in self._items and not allow_override and key not in self._overrides:
            raise PluginConflictError(
                f"protocol {key!r} already registered by {self._sources.get(key)!r}; "
                f"conflicting source {source!r}"
            )
        self._sources[key] = source
        self._items[key] = binder
        from tegel.register.binders._plugins import clear_plugin_conflict

        clear_plugin_conflict(key)

    def override(self, protocol: str, binder: Any) -> None:
        key = protocol.lower()
        self._overrides[key] = binder
        self.register(key, binder, source="override", allow_override=True)

    def get(self, protocol: str) -> Any:
        key = protocol.lower()
        from tegel.register.binders._plugins import get_plugin_conflicts

        conflicts = get_plugin_conflicts()
        if key in conflicts:
            left, right = conflicts[key]
            raise PluginConflictError(
                f"protocol {key!r} registered by both {left!r} and {right!r}"
            )
        if key in self._overrides:
            return self._materialize(key, self._overrides)
        if key not in self._items:
            raise UnknownSchemeError(f"no binder registered for protocol {key!r}")
        return self._materialize(key, self._items)

    def _materialize(self, key: str, store: dict[str, Any]) -> Any:
        item = store[key]
        if isinstance(item, LazyBinderProxy):
            try:
                item = item.load()
            except ImportError as exc:
                raise PluginError(f"failed to import binder for protocol {key!r}") from exc
            store[key] = item
            return item
        if isinstance(item, type):
            item = check_binder(item(), key)
            store[key] = item
            return item
        if callable(item) and not hasattr(item, "plan"):
            item = check_binder(item(), key)
            store[key] = item
            return item
        return check_binder(item, key)

    def protocols(self) -> frozenset[str]:
        return frozenset(self._items) | frozenset(self._overrides)

    def source_of(self, protocol: str) -> str | None:
        return self._sources.get(protocol.lower())


_GLOBAL = BinderRegistry()


def get_global_registry() -> BinderRegistry:
    return _GLOBAL


def register_binder(protocol: str, binder: Any, *, source: str = "explicit") -> None:
    _GLOBAL.register(protocol, binder, source=source)
