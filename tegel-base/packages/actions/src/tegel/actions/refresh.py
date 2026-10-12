"""Ask the data server to drop its pooled catalog connections."""

from __future__ import annotations

import os

import httpx


def refresh_catalog() -> None:
    """Call ``POST /refresh-catalog`` with a client-credentials token."""
    url = os.environ.get("TEGEL_REFRESH_URL", "").strip()
    issuer = os.environ.get("TEGEL_OIDC_ISSUER", "").strip()
    client_id = os.environ.get("TEGEL_OIDC_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TEGEL_OIDC_CLIENT_SECRET", "").strip()
    if not url or not issuer or not client_id or not client_secret:
        raise RuntimeError("TEGEL_REFRESH_URL and the OIDC client settings are required")
    token = _token(issuer, client_id, client_secret)
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get("refreshed") is not True:
        raise RuntimeError("the data server did not refresh the catalog")


def _token(issuer: str, client_id: str, client_secret: str) -> str:
    response = httpx.post(
        issuer.rstrip("/") + "/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("token response has no access_token")
    return token
