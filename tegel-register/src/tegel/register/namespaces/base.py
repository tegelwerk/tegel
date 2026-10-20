"""Namespace types and cache (§20)."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Protocol, runtime_checkable
from tegel.register.secrets.base import SecretsProvider

class ResolutionStatus(str, Enum):
    AVAILABLE = "available"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"

@dataclass(frozen=True)
class NamespaceResolution:
    status: ResolutionStatus
    uri: str | None = None
    expires_at: datetime | None = None
    etag: str | None = None
    retry_after: timedelta | None = None
    detail: str | None = None

@dataclass(frozen=True)
class Principal:
    id: str
    attributes: frozenset[str] = frozenset()

@dataclass(frozen=True)
class NamespaceRequestContext:
    principal: Principal | None
    secrets: SecretsProvider | None
    request_id: str

@runtime_checkable
class NamespaceResolver(Protocol):
    def resolve_namespace(self, identifier: str, context: NamespaceRequestContext) -> NamespaceResolution: ...

@dataclass
class _CacheEntry:
    resolution: NamespaceResolution
    stored_at: datetime

class NamespaceCache:
    def __init__(self, *, default_ttl: timedelta = timedelta(minutes=5)) -> None:
        self._default_ttl = default_ttl
        self._entries: dict[tuple[str, str, str | None], _CacheEntry] = {}

    def get(self, namespace: str, identifier: str, principal: str | None) -> NamespaceResolution | None:
        key = (namespace, identifier, principal)
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires = entry.resolution.expires_at or (entry.stored_at + self._default_ttl)
        if datetime.now(timezone.utc) >= expires:
            del self._entries[key]
            return None
        return entry.resolution

    def put(self, namespace: str, identifier: str, principal: str | None, resolution: NamespaceResolution) -> None:
        """Cache AVAILABLE, and UNAVAILABLE only for ``retry_after``.

        NOT_FOUND, RESTRICTED, and UNAVAILABLE without ``retry_after`` are not stored.
        """
        key = (namespace, identifier, principal)
        now = datetime.now(timezone.utc)
        if resolution.status is ResolutionStatus.AVAILABLE:
            stored = resolution
            if stored.expires_at is None:
                stored = NamespaceResolution(
                    status=resolution.status,
                    uri=resolution.uri,
                    expires_at=now + self._default_ttl,
                    etag=resolution.etag,
                    retry_after=resolution.retry_after,
                    detail=resolution.detail,
                )
            self._entries[key] = _CacheEntry(resolution=stored, stored_at=now)
            return
        if resolution.status is ResolutionStatus.UNAVAILABLE and resolution.retry_after is not None:
            stored = NamespaceResolution(
                status=resolution.status,
                uri=resolution.uri,
                expires_at=now + resolution.retry_after,
                etag=resolution.etag,
                retry_after=resolution.retry_after,
                detail=resolution.detail,
            )
            self._entries[key] = _CacheEntry(resolution=stored, stored_at=now)
            return
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
