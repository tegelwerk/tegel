"""Regressions for the review of the first version."""

from __future__ import annotations

import httpx
from conftest import TokenAuth, headers

ALICE = {"Authorization": "Bearer tok-alice"}


def test_a_refused_batch_queues_nothing(server) -> None:
    x = (server.data / "x.npy").as_uri()
    response = server.client.post(
        "/batches", json={"action": "test.read", "inputs": {"data": [x, "zenodo:/records/1/a"]}},
        headers=headers("alice"))
    assert response.status_code == 403
    assert response.json()["detail"].startswith("data[1]:")
    assert server.client.get("/runs", headers=headers("alice")).json() == []


def _outside(request: httpx.Request) -> httpx.Response:
    """An identity provider that refuses exchanges, and a file server without Range."""
    if request.url.host == "idp.example":
        return httpx.Response(400, json={"error": "invalid_target"})
    if request.method == "HEAD":
        return httpx.Response(405)
    return httpx.Response(200, headers={"content-length": "1000"}, content=b"x" * 1000)


def test_refused_exchange_is_a_plan_note_and_leaves_nothing_cached(make_server) -> None:
    server = make_server(
        auth=TokenAuth(), token_mode="exchange", exchange_url="https://idp.example/token",
        http_client=httpx.Client(transport=httpx.MockTransport(_outside)))
    body = {"action": "test.json", "inputs": {"doc": "https://files.example/doc.json"}}
    response = server.client.post("/plans", json=body, headers=ALICE)
    assert response.status_code == 200, response.text
    (step,) = response.json()["inputs"]
    assert step["size_bytes"] == 1000  # read from the headers of a streamed GET
    assert any("no token for https://files.example" in n for n in step["notes"])
    assert server.app.state.tegel_actions.broker._exchanged == {}
    run = server.run("alice", body, hdrs=ALICE)
    assert run["state"] == "failed" and "token exchange" in run["error"]
    assert server.app.state.tegel_actions.broker._exchanged == {}


def test_input_origins_limit_what_the_server_fetches(make_server) -> None:
    server = make_server(input_origins=["https://data.example"])
    body = {"action": "test.json", "inputs": {"doc": "http://169.254.169.254/latest/meta-data"}}
    plan = server.client.post("/plans", json=body, headers=headers("alice")).json()
    assert "TEGEL_INPUT_ORIGINS" in plan["inputs"][0]["notes"][0]
    run = server.run("alice", body)
    assert run["state"] == "failed" and "TEGEL_INPUT_ORIGINS" in run["error"]


def test_a_missing_result_file_is_a_plan_note(server) -> None:
    made = server.run("alice", {"action": "test.make", "parameters": {"n": 2}})
    resource = made["result"].rsplit("/", 1)[1]
    (server.config.outputs_dir / resource / "info.json").unlink()
    url = f"http://testserver/resources/{resource}/data/info/content"
    response = server.client.post("/plans", json={"action": "test.json",
                                                  "inputs": {"doc": url}},
                                  headers=headers("alice"))
    assert response.status_code == 200, response.text
    assert response.json()["inputs"][0]["notes"]


def test_system_exit_in_an_action_fails_the_run(server) -> None:
    run = server.run("alice", {"action": "test.exit"})
    assert run["state"] == "failed" and "SystemExit" in run["error"]
    again = server.run("alice", {"action": "test.sleep", "parameters": {"seconds": 0,
                                                                          "tag": "after-exit"}})
    assert again["state"] == "succeeded"
