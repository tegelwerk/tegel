"""Authoring, loading, API keys and the executor, without a server."""

from __future__ import annotations

import threading
import time

import pytest

import testactions
from tegel.actionserver import action, port
from tegel.actionserver.auth import ApiKeyAuth, has_scope, new_key
from tegel.actionserver.executor import Executor
from tegel.actionserver.loading import ActionLoadError, ActionTable, load_actions
from tegel.core.contracts.capabilities import Principal
from tegel.dataserver.auth.keycloak import AuthenticationError


def test_decorator_builds_card_and_parameter_schema() -> None:
    card = testactions.sleep.card
    assert card.ref == "test.sleep@1"
    props = card.parameters["properties"]
    assert props["seconds"]["default"] == 0.2 and props["seconds"]["type"] == "number"
    assert card.parameters["additionalProperties"] is False
    assert card.title == "Sleep, and record when."


def test_decorator_checks_ports_against_signature() -> None:
    with pytest.raises(TypeError, match="must match input ports"):
        @action("x.y", version="1", inputs=[port("a", "array")])
        def wrong(ctx, b):
            return {}


def test_run_validates_parameters() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        testactions.sleep.run(None, {}, {"seconds": "slow"})


def test_table_picks_highest_version_and_refuses_duplicates() -> None:
    @action("v.x", version="1.2.0")
    def old(ctx):
        return {}

    @action("v.x", version="1.10.0")
    def new(ctx):
        return {}

    table = ActionTable([old, new])
    assert table.get("v.x").card.version == "1.10.0"
    assert table.get("v.x@1.2.0") is old
    with pytest.raises(KeyError):
        table.get("v.x@2")
    with pytest.raises(ActionLoadError):
        ActionTable([old, old])


def test_installed_builtins_load_from_entry_points() -> None:
    table = load_actions()
    assert {a.card.id for a in table.current()} >= {"array.stats", "table.summary"}
    assert [a.card.id for a in load_actions("table.*").current()] == ["table.summary"]


def test_api_keys_are_compared_by_digest() -> None:
    key, digest = new_key()
    auth = ApiKeyAuth(keys=[{"sha256": digest, "subject": "ana", "scopes": ["actions:read"]}])
    assert auth.authenticate(f"Bearer {key}").subject == "ana"
    assert auth.authenticate(f"Apikey {key}").scopes == ["actions:read"]
    for bad in (None, "", f"Basic {key}", "Bearer wrong", "Bearer"):
        with pytest.raises(AuthenticationError):
            auth.authenticate(bad)
    with pytest.raises(ValueError):
        ApiKeyAuth(keys=[{"subject": "nokey"}])


def test_api_keys_from_file(tmp_path, monkeypatch) -> None:
    key, digest = new_key()
    path = tmp_path / "keys.yaml"
    path.write_text(f"keys:\n  - sha256: {digest}\n    subject: ana\n    scopes: [actions:run]\n")
    monkeypatch.setenv("TEGEL_API_KEYS", str(path))
    assert ApiKeyAuth().authenticate(f"Bearer {key}").subject == "ana"


def test_admin_implies_action_scopes_only() -> None:
    admin = Principal(subject="a", scopes=["actions:admin"])
    assert has_scope(admin, "actions:run") and has_scope(admin, "actions:bind")
    assert not has_scope(admin, "catalog:write")


def test_executor_runs_in_order_and_cancels_pending() -> None:
    executor = Executor(workers=1)
    order: list[str] = []
    gate, started = threading.Event(), threading.Event()
    executor.submit("a", [], lambda: (started.set(), gate.wait(5), order.append("a")))
    executor.submit("b", [], lambda: order.append("b"))
    executor.submit("c", [], lambda: order.append("c"))
    assert started.wait(5)
    assert executor.cancel("b") is True
    assert executor.cancel("a") is False  # started
    gate.set()
    assert executor.idle(5)
    assert order == ["a", "c"]
    executor.close()


def test_executor_slots_do_not_block_other_jobs() -> None:
    executor = Executor(workers=2, slots={"gpu": 1})
    spans: dict[str, tuple[float, float]] = {}

    def job(name: str, seconds: float):
        def run():
            start = time.monotonic()
            time.sleep(seconds)
            spans[name] = (start, time.monotonic())
        return run

    executor.submit("g1", ["gpu"], job("g1", 0.2))
    executor.submit("g2", ["gpu"], job("g2", 0.2))
    executor.submit("cpu", [], job("cpu", 0.05))
    assert executor.idle(5)
    assert spans["g2"][0] >= spans["g1"][1] - 1e-3
    assert spans["cpu"][0] < spans["g1"][1]
    executor.close()
