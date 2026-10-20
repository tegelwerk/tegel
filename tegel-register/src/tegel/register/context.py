"""BindContext: site, secrets, namespaces, and binder lookup for one session."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from tegel.register._uriparse import split_uri
from tegel.register.binders._registry import BinderRegistry, get_global_registry
from tegel.register.errors import (
    InvalidURIError,
    NamespaceNotFoundError,
    NamespaceResolutionError,
    NamespaceRestrictedError,
    NamespaceUnavailableError,
    ResolutionLoopError,
    URIResolverError,
)
from tegel.register.namespaces.base import (
    NamespaceCache,
    NamespaceRequestContext,
    NamespaceResolver,
    Principal,
    ResolutionStatus,
)
from tegel.register.site import Site


@dataclass
class NamespaceConfig:
    resolvers: dict[str, NamespaceResolver] = field(default_factory=dict)
    default_ttl_seconds: int = 300


@dataclass
class BindContext:
    site: Site | None = None
    secrets: Any | None = None
    namespaces: NamespaceConfig | None = None
    principal: Principal | None = None
    max_resolution_depth: int = 4
    namespace_cache: bool = True
    binders: BinderRegistry | None = None

    _closed: bool = field(default=False, init=False, repr=False)
    _cache: NamespaceCache | None = field(default=None, init=False, repr=False)
    _clients: dict[tuple, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.binders is None:
            self.binders = get_global_registry()
        if self.binders is get_global_registry():
            from tegel.register.binders._plugins import bootstrap

            bootstrap(self.binders)
        if self.site is None:
            self.site = Site.load()
        if self.secrets is None:
            self.secrets = _default_secrets()
        if self.namespace_cache:
            from datetime import timedelta

            ttl = self.namespaces.default_ttl_seconds if self.namespaces else 300
            self._cache = NamespaceCache(default_ttl=timedelta(seconds=ttl))

    @classmethod
    def default(cls) -> BindContext:
        """Process-wide lazily created instance."""
        global _DEFAULT
        if _DEFAULT is None or _DEFAULT.closed:
            _DEFAULT = cls()
        return _DEFAULT

    def __enter__(self) -> BindContext:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for client in list(self._clients.values()):
            closer = getattr(client, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass
        self._clients.clear()
        if self._cache is not None:
            self._cache.clear()

    def cache(self, key: tuple, factory: Callable[[], Any]) -> Any:
        """Return a context-scoped client. Closing the context drops it."""
        self._ensure_open()
        if key not in self._clients:
            self._clients[key] = factory()
        return self._clients[key]

    def resolve_chain(self, uri: str) -> tuple[str, tuple[str, ...]]:
        """Resolve namespace hops. Returns the concrete URI and the hop trail."""
        self._ensure_open()
        self._parse_uri(uri)
        return self._resolve_chain(uri, depth=0, seen=set(), trail=[])

    def _ensure_open(self) -> None:
        if self._closed:
            raise URIResolverError("BindContext is closed")

    def _parse_uri(self, uri: str):
        try:
            return split_uri(uri)
        except ValueError as exc:
            raise InvalidURIError(str(exc)) from exc

    def _resolve_chain(
        self, uri: str, *, depth: int, seen: set[str], trail: list[str]
    ) -> tuple[str, tuple[str, ...]]:
        if depth > self.max_resolution_depth:
            raise ResolutionLoopError(
                f"resolution depth exceeded max_resolution_depth={self.max_resolution_depth}"
            )
        parts = self._parse_uri(uri)
        key = parts.scheme + ":" + uri[len(parts.raw_scheme) + 1 :]
        if key in seen:
            raise ResolutionLoopError(f"resolution loop detected at {uri!r}")
        seen = set(seen)
        seen.add(key)
        scheme = parts.scheme
        if self.namespaces is not None and scheme in self.namespaces.resolvers:
            if parts.body.startswith("//"):
                raise InvalidURIError(
                    f"namespace scheme {scheme!r} defines no authority component; "
                    f"{uri!r} uses '//' after the scheme (RFC 7595 §3.2). "
                    f"Use the opaque form {scheme}:<identifier>."
                )
            resolution = self._resolve_namespace(scheme, parts.body)
            if resolution.status is ResolutionStatus.NOT_FOUND:
                raise NamespaceNotFoundError(f"namespace {scheme!r}: not found")
            if resolution.status is ResolutionStatus.RESTRICTED:
                raise NamespaceRestrictedError(resolution.detail or f"namespace {scheme!r}: restricted")
            if resolution.status is ResolutionStatus.UNAVAILABLE:
                raise NamespaceUnavailableError(
                    resolution.detail or f"namespace {scheme!r}: unavailable",
                    retry_after=resolution.retry_after,
                )
            if resolution.uri is None:
                raise NamespaceResolutionError(f"namespace {scheme!r} returned AVAILABLE without uri")
            target = _inherit_fragment(resolution.uri, parts.fragment)
            return self._resolve_chain(
                target, depth=depth + 1, seen=seen, trail=trail + [uri]
            )
        return uri, tuple(trail)

    def _resolve_namespace(self, namespace: str, identifier: str):
        assert self.namespaces is not None
        principal_id = self.principal.id if self.principal else None
        if self._cache is not None:
            cached = self._cache.get(namespace, identifier, principal_id)
            if cached is not None:
                return cached
        resolver = self.namespaces.resolvers[namespace]
        if getattr(resolver, "opaque_payload", False):
            from tegel.register.redaction import register_opaque_scheme

            register_opaque_scheme(namespace)
        request = NamespaceRequestContext(
            principal=self.principal, secrets=self.secrets, request_id=str(uuid.uuid4())
        )
        result = resolver.resolve_namespace(identifier, request)
        if self._cache is not None:
            self._cache.put(namespace, identifier, principal_id, result)
        return result


Context = BindContext
_DEFAULT: BindContext | None = None


def _default_secrets():
    from tegel.register.secrets.jsonfile import LocalSecretsManager, default_secrets_directory

    if default_secrets_directory().is_dir():
        return LocalSecretsManager.default()
    return None


def _inherit_fragment(target: str, fragment: str | None) -> str:
    """Carry a request fragment onto a namespace target.

    A target that names its own fragment keeps it. Otherwise the fragment of
    the request is inherited.
    """
    if fragment is None:
        return target
    if "#" in target:
        return target
    return f"{target}#{fragment}"
