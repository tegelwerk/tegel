"""Execution: phases, results as Tegel resources, failures, resources, restart."""

from __future__ import annotations

import json

import testactions
from conftest import headers


def _data(server, run: dict, who: str = "alice") -> dict:
    resource = run["result"].rsplit("/", 1)[1]
    response = server.client.get(f"/resources/{resource}/data", headers=headers(who))
    assert response.status_code == 200, response.text
    return response.json()


def _get(server, url: str, who: str = "alice", **params):
    return server.client.get(url.replace("http://testserver", ""), headers=headers(who),
                             params=params or None)


def test_outputs_become_parts_served_by_tiled_and_tegel(server) -> None:
    run = server.run("alice", {"action": "test.make", "parameters": {"n": 6}})
    assert run["state"] == "succeeded", run
    data = _data(server, run)
    parts = {p["name"]: p for p in data["parts"]}
    assert set(parts) == {"values", "info", "notes", "cell"}
    assert parts["values"]["served_by"] == "tiled" and parts["values"]["shape"] == [2, 3]
    array = _get(server, parts["values"]["content_url"], format="application/json").json()
    assert array == [[0.0, 1.0, 2.0], [3.0, 4.0, 5.0]]
    assert _get(server, parts["info"]["content_url"]).json() == {"n": 6}
    assert _get(server, parts["notes"]["content_url"]).text == "hello\n"
    assert parts["notes"]["media_type"] == "text/plain"
    assert _get(server, parts["cell"]["content_url"]).json()["lattice"][0] == [1, 0, 0]
    assert data["payload"]["media_type"] == "application/ld+json"


def test_record_quantities_and_search(server) -> None:
    x = (server.data / "x.npy").as_uri()
    run = server.run("alice", {"action": "array.stats", "inputs": {"data": x},
                               "parameters": {"unit": "K", "bins": 2}})
    resource = run["result"].rsplit("/", 1)[1]
    found = server.client.get(f"/resources/{resource}", headers=headers("reader")).json()
    record = found["metadata"]
    assert record["state"] == "succeeded" and record["principal"] == "alice"
    assert record["quantities"]["mean"]["numeric_value"] == 4.5
    assert record["quantities"]["mean"]["unit"] == "K"
    assert found["relations"] == [{"rel": "used", "target": x}]
    assert found["provenance"]["plugin_id"] == "array.stats@0.1.0"
    hits = server.client.get("/search", params={"action": "array.stats"},
                             headers=headers("reader"))
    assert hits.status_code == 200, hits.text
    assert resource in hits.text
    none = server.client.get("/search", params={"action": "table.summary"},
                             headers=headers("reader"))
    assert resource not in none.text


def test_crate_carries_prov(server) -> None:
    x = (server.data / "x.npy").as_uri()
    run = server.run("alice", {"action": "array.stats", "inputs": {"data": x}})
    crate = _get(server, _data(server, run)["payload"]["content_url"]).json()
    graph = {node["@id"]: node for node in crate["@graph"]}
    activity = graph[run["result"]]
    assert activity["prov:used"] == [{"@id": x}]
    assert activity["tegel:state"] == "succeeded"
    agent = graph[activity["prov:wasAssociatedWith"]["@id"]]
    assert agent["prov:actedOnBehalfOf"] == {"@id": "urn:tegel:principal:alice"}
    assert agent["tegel:software"]["numpy"]
    assert graph[activity["prov:hadPlan"]["@id"]]["version"] == "0.1.0"
    assert graph["mean"]["prov:wasGeneratedBy"] == {"@id": run["result"]}


def test_failed_run_is_stored_with_its_error(server) -> None:
    x = (server.data / "x.npy").as_uri()
    run = server.run("alice", {"action": "test.fail", "inputs": {"data": x}})
    assert run["state"] == "failed" and "boom" in run["error"]
    assert run["result"]
    data = _data(server, run)
    assert data["parts"] == []
    resource = run["result"].rsplit("/", 1)[1]
    record = server.client.get(f"/resources/{resource}", headers=headers("alice")).json()
    assert record["metadata"]["state"] == "failed" and "boom" in record["metadata"]["error"]
    crate = _get(server, data["payload"]["content_url"]).json()
    activity = next(n for n in crate["@graph"] if n["@id"] == run["result"])
    assert activity["tegel:notProduced"] == ["never"]


def test_failed_runs_are_not_reused(server) -> None:
    x = (server.data / "x.npy").as_uri()
    body = {"action": "test.fail", "inputs": {"data": x}}
    first, second = server.run("alice", body), server.run("alice", body)
    assert second["cached"] is False and second["id"] != first["id"]


def test_undeclared_output_fails_the_run(server) -> None:
    run = server.run("alice", {"action": "test.undeclared"})
    assert run["state"] == "failed" and "undeclared outputs ['b']" in run["error"]


def test_inputs_outside_readable_storage_fail_the_run(server, tmp_path) -> None:
    import numpy as np

    outside = tmp_path / "secret.npy"
    np.save(outside, np.arange(3))
    run = server.run("alice", {"action": "test.read", "inputs": {"data": outside.as_uri()}})
    assert run["state"] == "failed" and "not under readable storage" in run["error"]


def test_a_result_feeds_the_next_run(server) -> None:
    made = server.run("alice", {"action": "test.make", "parameters": {"n": 4}})
    parts = {p["name"]: p for p in _data(server, made)["parts"]}
    summed = server.run("alice", {"action": "test.read",
                                  "inputs": {"data": parts["values"]["content_url"]}})
    assert summed["state"] == "succeeded", summed
    total = _get(server, {p["name"]: p for p in _data(server, summed)["parts"]}["total"]
                 ["content_url"]).json()
    assert total["numeric_value"] == 6.0
    keys = server.run("alice", {"action": "test.json",
                                "inputs": {"doc": parts["info"]["content_url"]}})
    assert keys["state"] == "succeeded", keys
    resource = summed["result"].rsplit("/", 1)[1]
    found = server.client.get(f"/resources/{resource}", headers=headers("alice")).json()
    assert found["relations"] == [{"rel": "used", "target": parts["values"]["content_url"]}]


def test_selection_input(server) -> None:
    x = (server.data / "x.npy").as_uri()
    doc = (server.data / "doc.json").as_uri()
    run = server.run("alice", {"action": "test.many", "inputs": {"items": [x, doc]}})
    assert run["state"] == "succeeded", run
    resource = run["result"].rsplit("/", 1)[1]
    record = server.client.get(f"/resources/{resource}", headers=headers("alice")).json()
    assert record["metadata"]["quantities"]["count"]["numeric_value"] == 2.0
    assert len(record["relations"]) == 2


def test_resource_slots_serialize_gpu_actions(make_server) -> None:
    server = make_server(workers=3, resources={"gpu": 1})
    testactions.SPANS.clear()
    runs = [server.run("alice", {"action": "test.gpu", "parameters": {"seconds": 0.3,
                                                                        "tag": f"g{i}"}},
                       wait=False) for i in range(2)]
    free = server.run("alice", {"action": "test.sleep", "parameters": {"seconds": 0.3,
                                                                         "tag": "free"}},
                      wait=False)
    for run in [*runs, free]:
        done = server.client.post(f"/runs/{run['id']}/wait", params={"timeout": 30},
                                  headers=headers("alice")).json()
        assert done["state"] == "succeeded"
    spans = {tag: (start, end) for tag, start, end in testactions.SPANS}
    (a0, a1), (b0, b1) = spans["g0"], spans["g1"]
    assert a1 <= b0 or b1 <= a0, "two runs held the one gpu slot at once"
    f0, f1 = spans["free"]
    assert f0 < max(a1, b1), "an action without resources waited for the gpu"


def test_restart_fails_unfinished_runs(make_server, tmp_path) -> None:
    from tegel.actionserver.runs import RunStore
    from tegel.core.contracts.actions import RunState

    runs_db = tmp_path / "restart" / "runs.db"
    store = RunStore(runs_db)
    queued = store.add(action="test.sleep@1", inputs={}, parameters={}, principal="alice",
                       request_key="k1")
    running = store.add(action="test.sleep@1", inputs={}, parameters={}, principal="alice",
                        request_key="k2")
    store.move(running.id, RunState.running)
    store.close()
    server = make_server("restart", runs_db=runs_db)
    for run in (queued, running):
        got = server.client.get(f"/runs/{run.id}", headers=headers("alice")).json()
        assert got["state"] == "failed" and "restarted" in got["error"]
        assert got["ended"] and got["phase"] is None


def test_runs_db_and_rows_hold_no_token_material(make_server) -> None:
    server = make_server()
    x = (server.data / "x.npy").as_uri()
    server.run("alice", {"action": "array.stats", "inputs": {"data": x}})
    blob = server.config.runs_db.read_bytes()
    assert b"key-for-alice" not in blob
    for path in server.config.results_dir.rglob("*"):
        if path.is_file():
            assert b"key-for-alice" not in path.read_bytes(), path
    rows = json.dumps(server.client.get("/runs", headers=headers("alice")).json())
    assert "key-for-alice" not in rows
