"""Hold a caller's token for the run it belongs to, and decide what to send where.

The vault keeps tokens in memory only. Nothing in this module logs a token or
returns it in an error. In ``forward`` mode the caller's bearer token goes only
to origins listed in ``TEGEL_FORWARD_ORIGINS``. In ``exchange`` mode the server
asks the token endpoint for a token for that origin (RFC 8693). An API key is a
credential for this server alone and is never sent anywhere.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx

from tegel.actionserver.config import ActionServerConfig

EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


class TokenVault:
    """``run id -> Authorization header``, in memory, dropped when the run ends."""

    def __init__(self) -> None:
        self._held: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, run_id: str, authorization: str | None) -> None:
        if authorization:
            with self._lock:
                self._held[run_id] = authorization

    def get(self, run_id: str) -> str | None:
        with self._lock:
            return self._held.get(run_id)

    def drop(self, run_id: str) -> None:
        with self._lock:
            self._held.pop(run_id, None)

    def held(self) -> list[str]:
        """The tokens currently held, for redaction."""
        with self._lock:
            return [value.partition(" ")[2] for value in self._held.values()]


class ExchangeError(RuntimeError):
    """The token endpoint refused to issue a token for an origin."""


class TokenBroker:
    def __init__(
        self,
        config: ActionServerConfig,
        client: httpx.Client | None = None,
        *,
        provider_name: str | None = None,
    ) -> None:
        self.config = config
        self.provider_name = provider_name or config.auth_name
        self._client = client or httpx.Client(timeout=config.http_timeout)
        self._exchanged: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def authorization_for(self, authorization: str | None, origin: str) -> str | None:
        """The ``Authorization`` header to send to ``origin`` on behalf of the caller."""
        if not authorization or self.provider_name == "apikey":
            return None
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        if self.config.token_mode == "forward":
            return authorization if origin in self.config.forward_origins else None
        return f"Bearer {self._exchange(token, origin)}"

    def _exchange(self, subject_token: str, origin: str) -> str:
        key = (subject_token, origin)
        with self._lock:
            if key in self._exchanged:
                return self._exchanged[key]
        if not self.config.exchange_url:
            raise ExchangeError("TEGEL_TOKEN_EXCHANGE_URL is not set")
        auth: Any = None
        if self.config.exchange_client_id:
            auth = (self.config.exchange_client_id, self.config.exchange_client_secret or "")
        response = self._client.post(
            self.config.exchange_url,
            data={
                "grant_type": EXCHANGE_GRANT,
                "subject_token": subject_token,
                "subject_token_type": ACCESS_TOKEN_TYPE,
                "requested_token_type": ACCESS_TOKEN_TYPE,
                "audience": origin,
            },
            auth=auth,
        )
        if response.status_code != 200:
            raise ExchangeError(f"token exchange for {origin} returned HTTP {response.status_code}")
        issued = response.json().get("access_token")
        if not isinstance(issued, str) or not issued:
            raise ExchangeError(f"token exchange for {origin} returned no access_token")
        with self._lock:
            self._exchanged[key] = issued
        return issued

    def forget(self, authorization: str | None) -> None:
        """Drop exchanged tokens derived from this caller token."""
        if not authorization:
            return
        token = authorization.partition(" ")[2]
        with self._lock:
            for key in [k for k in self._exchanged if k[0] == token]:
                del self._exchanged[key]


def redact(text: str, secrets_: list[str]) -> str:
    for secret in secrets_:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[redacted]")
    return text
