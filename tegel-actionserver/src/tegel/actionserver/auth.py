"""API-key auth for a standalone server, and scope checks.

``ApiKeyAuth`` is registered as the ``apikey`` ``tegel.auth`` provider. It reads
``TEGEL_API_KEYS``, a YAML file of keys::

    keys:
      - sha256: 9f86d0...        # tegel-actionserver key prints a key and this line
        subject: ana
        scopes: [actions:read, actions:run]

A key is sent as ``Authorization: Bearer <key>`` or ``Apikey <key>``. The file
stores SHA-256 digests; a ``key:`` entry with the plain key is accepted for
local development.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Any

import yaml

from tegel.core.contracts.capabilities import Principal
from tegel.dataserver.auth.keycloak import AuthenticationError

READ = "actions:read"
RUN = "actions:run"
ADMIN = "actions:admin"
CATALOG = "catalog:write"


class ApiKeyAuth:
    """``tegel.auth`` provider that accepts keys listed in ``TEGEL_API_KEYS``."""

    name = "apikey"

    def __init__(self, keys: list[dict[str, Any]] | None = None, path: str | None = None) -> None:
        if keys is None:
            source = path or os.environ.get("TEGEL_API_KEYS", "").strip()
            if not source:
                raise ValueError("TEGEL_API_KEYS is required for the apikey auth provider")
            loaded = yaml.safe_load(Path(source).read_text(encoding="utf-8")) or {}
            keys = loaded.get("keys") or []
        self._entries: list[tuple[str, Principal]] = []
        for index, entry in enumerate(keys):
            digest = entry.get("sha256") or (
                _digest(entry["key"]) if entry.get("key") else None
            )
            if not digest or not entry.get("subject"):
                raise ValueError(f"TEGEL_API_KEYS entry {index} needs sha256 (or key) and subject")
            scopes = entry.get("scopes") or []
            if not isinstance(scopes, list):
                raise ValueError(f"TEGEL_API_KEYS entry {index}: scopes must be a list")
            principal = Principal(subject=str(entry["subject"]), scopes=[str(s) for s in scopes])
            self._entries.append((str(digest).lower(), principal))

    def authenticate(self, authorization: str | None) -> Principal:
        if not authorization:
            raise AuthenticationError("Authentication required")
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() not in ("bearer", "apikey") or not credential.strip():
            raise AuthenticationError("Authentication required")
        digest = _digest(credential.strip())
        for known, principal in self._entries:
            if hmac.compare_digest(known, digest):
                return principal
        raise AuthenticationError("Authentication required")


def new_key() -> tuple[str, str]:
    """A fresh random key and its SHA-256 digest."""
    import secrets

    key = secrets.token_urlsafe(32)
    return key, _digest(key)


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def has_scope(principal: Principal, scope: str) -> bool:
    """``actions:admin`` holds every ``actions:*`` scope; nothing else is implied."""
    if scope in principal.scopes:
        return True
    return ADMIN in principal.scopes and scope.startswith("actions:")
