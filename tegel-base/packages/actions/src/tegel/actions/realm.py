"""Register the realm, the client, and the reader user."""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx

from tegel.actions.credentials import credential_path, load_credentials

REALM = "tegel"
CLIENT_ID = "tegel"
READER = "reader"


def redirect_uris(origin: str, data_path: str) -> list[str]:
    """Browser redirects for the data server prefix."""
    base = origin.rstrip("/") + "/" + data_path.strip("/")
    return [f"{base}/*"]


def main() -> None:
    """Wait for Keycloak, then register redirect URIs for the public data prefix."""
    origin = _required("PUBLIC_ORIGIN")
    data_path = _required("PATH_DATA")
    internal = _required("KEYCLOAK_INTERNAL").rstrip("/")
    creds = load_credentials(credential_path())
    print(f"Configuring Keycloak realm {REALM} at {internal}", flush=True)
    admin = _admin_token(internal, creds["admin_password"])
    headers = {"Authorization": f"Bearer {admin}"}
    with httpx.Client(base_url=internal, timeout=30.0, headers=headers) as client:
        _ensure_realm(client)
        _ensure_client(client, creds["client_secret"], redirect_uris(origin, data_path), origin)
        _ensure_reader(client, creds["user_password"])
    print(f"Realm {REALM} is ready", flush=True)


def _ensure_realm(client: httpx.Client) -> None:
    existing = client.get(f"/admin/realms/{REALM}")
    if existing.status_code == 404:
        created = client.post(
            "/admin/realms",
            json={"realm": REALM, "enabled": True, "sslRequired": "external"},
        )
        if created.status_code >= 400:
            raise SystemExit(f"Keycloak realm create failed: {created.status_code} {created.text}")
        return
    if existing.status_code >= 400:
        raise SystemExit(f"Keycloak realm check failed: {existing.status_code}")


def _ensure_client(
    client: httpx.Client,
    client_secret: str,
    uris: list[str],
    origin: str,
) -> None:
    found = client.get(f"/admin/realms/{REALM}/clients", params={"clientId": CLIENT_ID})
    found.raise_for_status()
    clients = found.json()
    body: dict[str, Any] = {
        "clientId": CLIENT_ID,
        "enabled": True,
        "protocol": "openid-connect",
        "publicClient": False,
        "secret": client_secret,
        "directAccessGrantsEnabled": True,
        "serviceAccountsEnabled": True,
        "standardFlowEnabled": True,
        "redirectUris": uris,
        "webOrigins": [origin.rstrip("/")],
    }
    if clients:
        client_uuid = str(clients[0]["id"])
        updated = client.put(f"/admin/realms/{REALM}/clients/{client_uuid}", json=body)
        if updated.status_code >= 400:
            raise SystemExit(f"Keycloak client update failed: {updated.status_code} {updated.text}")
        return
    made = client.post(f"/admin/realms/{REALM}/clients", json=body)
    if made.status_code >= 400:
        raise SystemExit(f"Keycloak client create failed: {made.status_code} {made.text}")
    found = client.get(f"/admin/realms/{REALM}/clients", params={"clientId": CLIENT_ID})
    found.raise_for_status()
    clients = found.json()
    if not clients:
        raise SystemExit("Keycloak did not return the new client")
    mapped = client.post(
        f"/admin/realms/{REALM}/clients/{clients[0]['id']}/protocol-mappers/models",
        json={
            "name": "audience",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-audience-mapper",
            "config": {
                "included.client.audience": CLIENT_ID,
                "access.token.claim": "true",
                "id.token.claim": "false",
            },
        },
    )
    if mapped.status_code >= 400:
        raise SystemExit(f"Keycloak audience mapper failed: {mapped.status_code} {mapped.text}")


def _ensure_reader(client: httpx.Client, user_password: str) -> None:
    listed = client.get(
        f"/admin/realms/{REALM}/users",
        params={"username": READER, "exact": "true"},
    )
    listed.raise_for_status()
    users = listed.json()
    profile = {
        "username": READER,
        "enabled": True,
        "emailVerified": True,
        "email": "reader@localhost",
        "firstName": "Reader",
        "lastName": "Local",
        "requiredActions": [],
    }
    if users:
        user_id = str(users[0]["id"])
        updated = client.put(f"/admin/realms/{REALM}/users/{user_id}", json=profile)
    else:
        updated = client.post(f"/admin/realms/{REALM}/users", json=profile)
        listed = client.get(
            f"/admin/realms/{REALM}/users",
            params={"username": READER, "exact": "true"},
        )
        listed.raise_for_status()
        users = listed.json()
        user_id = str(users[0]["id"]) if users else ""
    if updated.status_code >= 400 or not user_id:
        raise SystemExit(f"Keycloak user setup failed: {updated.status_code} {updated.text}")
    password = client.put(
        f"/admin/realms/{REALM}/users/{user_id}/reset-password",
        json={"type": "password", "value": user_password, "temporary": False},
    )
    if password.status_code >= 400:
        raise SystemExit(f"Keycloak password set failed: {password.status_code} {password.text}")
    cleared = client.put(f"/admin/realms/{REALM}/users/{user_id}", json=profile)
    if cleared.status_code >= 400:
        raise SystemExit(f"Keycloak user setup failed: {cleared.status_code} {cleared.text}")


def _admin_token(internal: str, password: str) -> str:
    deadline = time.time() + 180
    last = ""
    url = internal + "/realms/master/protocol/openid-connect/token"
    while time.time() < deadline:
        try:
            response = httpx.post(
                url,
                data={
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": "admin",
                    "password": password,
                },
                timeout=5.0,
            )
        except httpx.HTTPError as exc:
            last = str(exc)
            print(f"Keycloak admin login not ready: {last}", flush=True)
            time.sleep(1)
            continue
        if response.status_code < 400:
            token = response.json().get("access_token")
            if isinstance(token, str) and token:
                return token
        if response.status_code in {400, 401}:
            raise SystemExit(
                "Keycloak rejected the admin password in TEGEL_OUTPUT. "
                "The Keycloak data volume was created with a different password."
            )
        last = f"{response.status_code} {response.text}"
        print(f"Keycloak admin login not ready: {last}", flush=True)
        time.sleep(1)
    raise SystemExit(f"Keycloak admin login failed: {last}")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(exc, file=sys.stderr)
        raise
