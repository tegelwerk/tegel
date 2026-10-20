"""The HTTP contract: auth, scopes, validation, visibility, caching, batches, cancel."""

from __future__ import annotations

import time

from conftest import headers


def test_unauthenticated_is_401(server) -> None:
    for method, path in [("get", "/actions"), ("get", "/runs"), ("post", "/runs"),
                         ("get", "/search"), ("get", "/capabilities")]:
        response = getattr(server.client, method)(path)
        assert response.status_code == 401, path
    assert server.client.get("/actions", headers={"Authorization": "Bearer nope"}).status_code \
        == 401


def test_capabilities_advertise_actions(server) -> None:
    body = server.client.get("/capabilities", headers=headers("reader")).json()
    assert body["services"] == ["read", "actions"]
    assert body["schema_id"] == "https://w3id.org/tegel/actionserver/run"
    assert body["class"] == "ActionRun"


def test_scopes_gate_listing_and_running(server) -> None:
    ids = {c["id"] for c in server.client.get("/actions", headers=headers("alice")).json()}
    assert "array.stats" in ids and "test.ingest" not in ids
    admin = {c["id"] for c in server.client.get("/actions", headers=headers("admin")).json()}
    assert "test.ingest" in admin
    assert server.client.get("/actions", headers=headers("runner")).status_code == 403
    response = server.client.post("/runs", json={"action": "test.sleep"},
                                  headers=headers("reader"))
    assert response.status_code == 403
    response = server.client.post("/runs", json={"action": "test.ingest"},
                                  headers=headers("alice"))
    assert response.status_code == 403
    assert server.client.get("/actions/test.ingest", headers=headers("alice")).status_code == 404
    card = server.client.get("/actions/array.stats", headers=headers("alice")).json()
    assert card["parameters"]["properties"]["bins"]["default"] == 20


def test_catalog_action_runs_with_catalog_scope(server) -> None:
    run = server.run("admin", {"action": "test.ingest"})
    assert run["state"] == "succeeded"


def test_bad_requests_are_400_and_queue_nothing(server) -> None:
    x = (server.data / "x.npy").as_uri()
    cases = [
        {"action": "no.such"},
        {"action": "array.stats"},
        {"action": "array.stats", "inputs": {"data": x, "extra": x}},
        {"action": "array.stats", "inputs": {"data": ""}},
        {"action": "array.stats", "inputs": {"data": x}, "parameters": {"bins": "many"}},
        {"action": "array.stats", "inputs": {"data": x}, "parameters": {"colour": 1}},
        {"action": "test.many", "inputs": {"items": []}},
        {"action": "array.stats@9.9", "inputs": {"data": x}},
    ]
    for body in cases:
        response = server.client.post("/runs", json=body, headers=headers("alice"))
        assert response.status_code == 400, (body, response.text)
        assert response.json()["detail"]
    assert server.client.get("/runs", headers=headers("alice")).json() == []


def test_run_records_defaults_and_version(server) -> None:
    x = (server.data / "x.npy").as_uri()
    run = server.run("alice", {"action": "array.stats", "inputs": {"data": x}})
    assert run["action"] == "array.stats@0.1.0"
    assert run["parameters"] == {"bins": 20, "unit": None}
    assert run["state"] == "succeeded" and run["phase"] is None
    assert run["result"] == f"http://testserver/resources/{run['id']}"
    assert run["started"] and run["ended"]


def test_runs_are_visible_to_their_submitter_and_admin(server) -> None:
    run = server.run("alice", {"action": "test.sleep", "parameters": {"seconds": 0}})
    assert server.client.get(f"/runs/{run['id']}", headers=headers("bob")).status_code == 404
    assert server.client.get(f"/runs/{run['id']}", headers=headers("admin")).status_code == 200
    assert [r["id"] for r in server.client.get("/runs", headers=headers("bob")).json()] == []
    mine = server.client.get("/runs", params={"action": "test.sleep"},
                             headers=headers("alice")).json()
    assert [r["id"] for r in mine] == [run["id"]]
    assert server.client.get("/runs/nope", headers=headers("alice")).status_code == 404


def test_cache_is_per_principal_and_force_bypasses_it(server) -> None:
    body = {"action": "test.sleep", "parameters": {"seconds": 0, "tag": "cache"}}
    first = server.run("alice", body)
    second = server.run("alice", body)
    assert second["cached"] is True and second["result"] == first["result"]
    assert second["state"] == "succeeded"
    forced = server.run("alice", {**body, "force": True})
    assert forced["cached"] is False and forced["result"] != first["result"]
    other = server.run("bob", body)
    assert other["cached"] is False


def test_batch_fans_out_over_one_input(server) -> None:
    x = (server.data / "x.npy").as_uri()
    response = server.client.post(
        "/batches", json={"action": "test.read", "inputs": {"data": [x, x + "#again"]}},
        headers=headers("alice"))
    assert response.status_code == 202, response.text
    batch = response.json()
    assert len(batch["runs"]) == 2
    listed = server.client.get("/runs", params={"batch": batch["batch"]},
                               headers=headers("alice")).json()
    assert {r["id"] for r in listed} == {r["id"] for r in batch["runs"]}
    bad = server.client.post("/batches", json={"action": "test.read", "inputs": {"data": x}},
                             headers=headers("alice"))
    assert bad.status_code == 400


def test_cancel_only_queued_runs(make_server) -> None:
    server = make_server(workers=1)
    long = server.run("alice", {"action": "test.sleep", "parameters": {"seconds": 1.0,
                                                                         "tag": "long"}},
                      wait=False)
    queued = server.run("alice", {"action": "test.sleep", "parameters": {"seconds": 0,
                                                                           "tag": "q"}},
                        wait=False)
    deadline = time.monotonic() + 5
    while server.client.get(f"/runs/{long['id']}", headers=headers("alice")).json()["state"] \
            != "running" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.client.delete(f"/runs/{queued['id']}", headers=headers("bob")).status_code \
        == 404
    cancelled = server.client.delete(f"/runs/{queued['id']}", headers=headers("alice"))
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    running = server.client.delete(f"/runs/{long['id']}", headers=headers("alice"))
    assert running.status_code == 409
    done = server.client.post(f"/runs/{long['id']}/wait", params={"timeout": 30},
                              headers=headers("alice")).json()
    assert done["state"] == "succeeded"


def test_wait_times_out_on_a_queued_run(make_server) -> None:
    server = make_server(workers=1)
    server.run("alice", {"action": "test.sleep", "parameters": {"seconds": 0.8, "tag": "a"}},
               wait=False)
    queued = server.run("alice", {"action": "test.sleep", "parameters": {"seconds": 0,
                                                                           "tag": "b"}},
                        wait=False)
    got = server.client.post(f"/runs/{queued['id']}/wait", params={"timeout": 0.05},
                             headers=headers("alice")).json()
    assert got["state"] in ("queued", "running")


def test_read_side_needs_actions_read(server) -> None:
    run = server.run("alice", {"action": "test.sleep", "parameters": {"seconds": 0}})
    resource = run["result"].rsplit("/", 1)[1]
    assert server.client.get(f"/resources/{resource}", headers=headers("reader")).status_code \
        == 200
    assert server.client.get(f"/resources/{resource}", headers=headers("runner")).status_code \
        == 403
    assert server.client.get("/api/v1/metadata/", headers=headers("runner")).status_code == 403
    assert server.client.post("/refresh-catalog", headers=headers("alice")).status_code == 403
