"""Stage phase across servers: routes, token forwarding, transfer plans, binds."""

from __future__ import annotations

import logging

import httpx
import pytest

from conftest import TokenAuth
from tegel.actionserver.config import ActionServerConfig
from tegel.actionserver.tokens import TokenBroker

ALICE = {"Authorization": "Bearer tok-alice"}


@pytest.fixture
def remote(make_server):
    server = make_server("remote", origin="http://remote", auth=TokenAuth())
    made = server.run("alice", {"action": "test.make", "parameters": {"n": 6}}, hdrs=ALICE)
    assert made["state"] == "succeeded"
    resource = made["result"].rsplit("/", 1)[1]
    data = server.client.get(f"/resources/{resource}/data", headers=ALICE).json()
    server.parts = {p["name"]: p["content_url"] for p in data["parts"]}
    return server


def _local(make_server, remote, name="local", **overrides):
    overrides.setdefault("forward_origins", ["http://remote"])
    return make_server(name, auth=TokenAuth(), http_client=remote.client, **overrides)


def test_tiled_node_on_another_server_is_read_with_the_callers_token(make_server, remote,
                                                                     caplog) -> None:
    caplog.set_level(logging.DEBUG)
    local = _local(make_server, remote)
    body = {"action": "test.read", "inputs": {"data": remote.parts["values"]}}
    plan = local.client.post("/plans", json=body, headers=ALICE).json()
    (step,) = plan["inputs"]
    assert step["route"] == "tiled" and step["source"] == "http://remote"
    assert step["size_bytes"] == 48 and step["moves_bytes"] is True
    run = local.run("alice", body, hdrs=ALICE)
    assert run["state"] == "succeeded", run
    resource = run["result"].rsplit("/", 1)[1]
    record = local.client.get(f"/resources/{resource}", headers=ALICE).json()["metadata"]
    assert record["quantities"]["total"]["numeric_value"] == 15.0
    # The token reached the remote server and nowhere else.
    assert "tok-alice" not in caplog.text
    for path in local.config.results_dir.rglob("*"):
        if path.is_file():
            assert b"tok-alice" not in path.read_bytes(), path
    assert local.app.state.tegel_actions.vault.held() == []
    learned = local.app.state.tegel_actions.store.throughput("http://remote")
    assert learned and learned > 0


def test_token_is_not_sent_to_an_origin_that_is_not_listed(make_server, remote) -> None:
    local = _local(make_server, remote, "unlisted", forward_origins=[])
    run = local.run("alice", {"action": "test.read", "inputs": {"data": remote.parts["values"]}},
                    hdrs=ALICE)
    assert run["state"] == "failed"
    assert "tok-alice" not in run["error"] and "http://remote" in run["error"]


def test_byte_part_on_another_server(make_server, remote) -> None:
    local = _local(make_server, remote, "bytes")
    body = {"action": "test.json", "inputs": {"doc": remote.parts["info"]}}
    plan = local.client.post("/plans", json=body, headers=ALICE).json()
    (step,) = plan["inputs"]
    assert step["route"] == "bytes" and step["size_bytes"] > 0, step
    run = local.run("alice", body, hdrs=ALICE)
    assert run["state"] == "succeeded", run


def test_slow_transfer_is_refused_with_the_plan_until_allowed(make_server, remote) -> None:
    local = _local(make_server, remote, "slow", transfer_warn_seconds=1, default_throughput=1)
    body = {"action": "test.read", "inputs": {"data": remote.parts["values"]}}
    refused = local.client.post("/runs", json=body, headers=ALICE)
    assert refused.status_code == 409
    plan = refused.json()["plan"]
    assert plan["transfer_first"] is True and plan["seconds"] == pytest.approx(48.0)
    assert "allow_transfer" in refused.json()["detail"]
    assert local.client.get("/runs", headers=ALICE).json() == []
    run = local.run("alice", {**body, "allow_transfer": True}, hdrs=ALICE)
    assert run["state"] == "succeeded", run
    again = local.client.post("/plans", json={**body, "force": True}, headers=ALICE).json()
    assert again["inputs"][0]["throughput_measured"] is True
    assert again["transfer_first"] is False
    cached = local.client.post("/plans", json=body, headers=ALICE).json()
    assert cached["cached"] == run["result"] and cached["inputs"] == []


def test_local_inputs_move_nothing(server) -> None:
    x = (server.data / "x.npy").as_uri()
    plan = server.client.post("/plans", json={"action": "test.read", "inputs": {"data": x}},
                              headers={"Authorization": "Bearer key-for-alice"}).json()
    (step,) = plan["inputs"]
    assert step["route"] == "local" and step["moves_bytes"] is False
    assert step["size_bytes"] == (server.data / "x.npy").stat().st_size
    assert plan["seconds"] == 0.0 and plan["transfer_first"] is False


def test_binding_needs_the_bind_scope(server) -> None:
    body = {"action": "test.read", "inputs": {"data": "zenodo:/records/1/a.npy"}}
    alice = {"Authorization": "Bearer key-for-alice"}
    plan = server.client.post("/plans", json=body, headers=alice).json()
    assert plan["inputs"][0]["route"] == "bind"
    assert any("actions:bind" in w for w in plan["warnings"])
    refused = server.client.post("/runs", json=body, headers=alice)
    assert refused.status_code == 403
    run = server.run("binder", body)
    assert run["state"] == "failed" and "binding zenodo:/records/1/a.npy failed" in run["error"]


def _config(**kw) -> ActionServerConfig:
    return ActionServerConfig(public_origin="http://here", results_dir="/tmp/unused", **kw)


def test_forwarding_rules() -> None:
    broker = TokenBroker(_config(forward_origins=["https://data.example"]),
                         provider_name="keycloak")
    assert broker.authorization_for("Bearer t0k", "https://data.example") == "Bearer t0k"
    assert broker.authorization_for("Bearer t0k", "https://other.example") is None
    assert broker.authorization_for("Basic x", "https://data.example") is None
    keys = TokenBroker(_config(forward_origins=["https://data.example"]), provider_name="apikey")
    assert keys.authorization_for("Bearer key", "https://data.example") is None


def test_token_exchange_asks_for_the_target_audience() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, json={"access_token": "exchanged-for-" +
                                         seen[-1]["audience"]})

    broker = TokenBroker(
        _config(token_mode="exchange", exchange_url="https://idp.example/token",
                exchange_client_id="tegel-actions", exchange_client_secret="s"),
        client=httpx.Client(transport=httpx.MockTransport(handler)), provider_name="keycloak")
    header = broker.authorization_for("Bearer subject-token", "https://data.example")
    assert header == "Bearer exchanged-for-https://data.example"
    assert seen[0]["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert seen[0]["subject_token"] == "subject-token"
    broker.authorization_for("Bearer subject-token", "https://data.example")
    assert len(seen) == 1  # reused for the run
    broker.forget("Bearer subject-token")
    broker.authorization_for("Bearer subject-token", "https://data.example")
    assert len(seen) == 2
