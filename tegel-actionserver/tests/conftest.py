from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))

import testactions  # noqa: E402
from tegel.actionserver import builtin  # noqa: E402
from tegel.actionserver.app import create_app  # noqa: E402
from tegel.actionserver.auth import ApiKeyAuth  # noqa: E402
from tegel.actionserver.config import ActionServerConfig  # noqa: E402
from tegel.actionserver.loading import ActionTable  # noqa: E402
from tegel.core.contracts.capabilities import Principal  # noqa: E402
from tegel.dataserver.auth.keycloak import AuthenticationError  # noqa: E402

KEYS = {
    "alice": ["actions:read", "actions:run"],
    "bob": ["actions:read", "actions:run"],
    "reader": ["actions:read"],
    "runner": ["actions:run"],
    "admin": ["actions:admin", "catalog:write"],
    "binder": ["actions:read", "actions:run", "actions:bind"],
}


def headers(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer key-for-{subject}"}


def api_keys() -> ApiKeyAuth:
    return ApiKeyAuth(keys=[{"key": f"key-for-{s}", "subject": s, "scopes": scopes}
                            for s, scopes in KEYS.items()])


class TokenAuth:
    """An OIDC-like provider for tests: ``Bearer tok-<subject>`` with the KEYS scopes."""

    name = "test-oidc"

    def authenticate(self, authorization: str | None) -> Principal:
        scheme, _, token = (authorization or "").partition(" ")
        subject = token.removeprefix("tok-")
        if scheme.lower() != "bearer" or not token.startswith("tok-") or subject not in KEYS:
            raise AuthenticationError("Authentication required")
        return Principal(subject=subject, scopes=KEYS[subject])


@dataclass
class Server:
    client: TestClient
    app: Any
    config: ActionServerConfig
    data: Path

    def run(self, who: str, body: dict, *, wait: bool = True, hdrs: dict | None = None) -> dict:
        response = self.client.post("/runs", json=body, headers=hdrs or headers(who))
        assert response.status_code == 202, response.text
        run = response.json()
        if wait:
            run = self.client.post(f"/runs/{run['id']}/wait", params={"timeout": 60},
                                   headers=hdrs or headers(who)).json()
        return run


@pytest.fixture
def make_server(tmp_path: Path) -> Iterator[Callable[..., Server]]:
    made: list[tuple[TestClient, Any]] = []

    def factory(name: str = "srv", *, origin: str = "http://testserver", auth: Any = None,
                **overrides: Any) -> Server:
        data = tmp_path / f"{name}-data"
        data.mkdir(exist_ok=True)
        np.save(data / "x.npy", np.arange(10.0))
        (data / "doc.json").write_text('{"a": 1, "b": 2}')
        clients = {k: overrides.pop(k) for k in ("http_client", "bind_context")
                   if k in overrides}
        config = ActionServerConfig(public_origin=origin, results_dir=tmp_path / name,
                                    readable_storage=[data], **overrides)
        actions = ActionTable([*testactions.ALL, builtin.array_stats, builtin.table_summary])
        app = create_app(config, actions=actions, auth=auth or api_keys(), **clients)
        client = TestClient(app, base_url=origin)
        client.__enter__()
        made.append((client, app))
        return Server(client, app, config, data)

    yield factory
    for client, app in made:
        app.state.tegel_actions.close()
        client.__exit__(None, None, None)


@pytest.fixture
def server(make_server) -> Server:
    return make_server()
