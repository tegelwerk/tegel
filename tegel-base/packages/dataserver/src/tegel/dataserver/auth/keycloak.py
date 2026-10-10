"""Keycloak bearer tokens, validated from the issuer's discovery document."""

from __future__ import annotations

import os
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from tegel.core.contracts.capabilities import Principal


class AuthenticationError(Exception):
    """The bearer token is missing or was rejected."""


class KeycloakAuth:
    """Reference ``tegel.auth`` provider.

    A no-argument constructor reads ``TEGEL_OIDC_ISSUER``,
    ``TEGEL_OIDC_AUDIENCE``, and ``TEGEL_OIDC_CLIENT_ID``. The launch process
    passes those values from ``ServerConfig`` instead. There is no default
    issuer.
    """

    name = "keycloak"

    def __init__(
        self,
        issuer_url: str | None = None,
        audience: str | None = None,
        client_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.issuer_url = _required(issuer_url, "TEGEL_OIDC_ISSUER")
        self.audience = _required(audience, "TEGEL_OIDC_AUDIENCE")
        self.client_id = _required(client_id, "TEGEL_OIDC_CLIENT_ID")
        self._client = client or httpx.Client(timeout=30.0)
        self._owns_client = client is None
        self._jwks: PyJWKClient | None = None

    def authenticate(self, authorization: str | None) -> Principal:
        """Accept a bearer token signed by the configured issuer."""
        token = _bearer(authorization)
        try:
            claims = jwt.decode(
                token,
                key=self._signing_key(token),
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer_url,
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Authentication required") from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise AuthenticationError("Authentication required")
        return Principal(subject=subject)

    def _signing_key(self, token: str) -> Any:
        if self._jwks is None:
            document = self._discovery()
            jwks_uri = document.get("jwks_uri")
            if not isinstance(jwks_uri, str) or not jwks_uri:
                raise AuthenticationError("Authentication required")
            self._jwks = PyJWKClient(jwks_uri)
        return self._jwks.get_signing_key_from_jwt(token).key

    def _discovery(self) -> dict[str, Any]:
        url = self.issuer_url.rstrip("/") + "/.well-known/openid-configuration"
        response = self._client.get(url)
        if response.status_code >= 400:
            raise AuthenticationError("Authentication required")
        payload = response.json()
        if not isinstance(payload, dict):
            raise AuthenticationError("Authentication required")
        return payload

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _required(value: str | None, env_name: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    found = os.environ.get(env_name, "").strip()
    if not found:
        raise ValueError(f"{env_name} is required")
    return found


def _bearer(authorization: str | None) -> str:
    if authorization is None:
        raise AuthenticationError("Authentication required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Authentication required")
    return token.strip()
