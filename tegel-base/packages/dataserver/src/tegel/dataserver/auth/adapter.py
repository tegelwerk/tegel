"""Present the one selected auth provider to Tiled.

A site registers a ``tegel.auth`` provider. This adapter is the only
authenticator Tiled receives. Keycloak is that provider when the server is
left on its default.
"""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import Depends, HTTPException, Request
from fastapi.security import SecurityScopes
from jwt.exceptions import ExpiredSignatureError
from tiled.server.protocols import ExternalAuthenticator, UserSessionState

from tegel.dataserver.auth.keycloak import AuthenticationError
from tegel.dataserver.base import AuthProvider

_active: ContextVar[AuthProvider] = ContextVar("tegel_auth_provider")


class TiledAuthAdapter(ExternalAuthenticator):
    """Tiled login and bearer checks call ``provider.authenticate``."""

    def __init__(self, provider: AuthProvider | None = None) -> None:
        self.provider = provider if provider is not None else _active.get()
        self.redirect_on_success = None
        self.redirect_on_failure = None

    async def authenticate(self, request: Request) -> UserSessionState | None:
        """Accept the request when the provider accepts its bearer token."""
        try:
            principal = self.provider.authenticate(request.headers.get("authorization"))
        except AuthenticationError:
            return None
        return UserSessionState(principal.subject, {})


def use_provider(provider: AuthProvider) -> object:
    """Make ``provider`` available while Tiled constructs the adapter."""
    return _active.set(provider)


def reset_provider(token: object) -> None:
    """Drop the provider set by ``use_provider``."""
    _active.reset(token)  # type: ignore[arg-type]


def install_bearer_check(app: object, provider: AuthProvider) -> None:
    """Decode bearer tokens with the same provider the read API uses.

    Tiled session tokens, signed by the server secret, still decode first.
    A token the provider rejects is not tried against Keycloak.
    """
    from tiled.server.authentication import decode_token, get_decoded_access_token, oauth2_scheme
    from tiled.server.settings import get_settings

    def claims(access_token: str, secret_keys: list[str]) -> dict[str, str]:
        try:
            payload = decode_token(access_token, secret_keys, None)
        except ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=401, detail="Access token has expired. Refresh token."
            ) from exc
        except HTTPException:
            payload = None
        if isinstance(payload, dict):
            return payload
        try:
            principal = provider.authenticate(f"Bearer {access_token}")
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail="Could not validate credentials") from exc
        return {
            "sub": principal.subject,
            "user": principal.subject,
            "scope": "read:metadata read:data",
        }

    async def decoded(
        request: Request,
        security_scopes: SecurityScopes,
        access_token: str | None = Depends(oauth2_scheme),
        settings: object = Depends(get_settings),
    ) -> dict[str, str] | None:
        del request, security_scopes
        if not access_token:
            return None
        return claims(access_token, settings.secret_keys)  # type: ignore[attr-defined]

    app.dependency_overrides[get_decoded_access_token] = decoded  # type: ignore[attr-defined]
