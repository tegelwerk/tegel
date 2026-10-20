"""Action server wire contracts, taken from specs/actionserver.md."""

import pytest
from pydantic import ValidationError

from tegel.core.contracts import (
    ActionCard,
    BatchRequest,
    Run,
    RunRequest,
    RunState,
    batch_port,
    can_transition,
    check_inputs,
    request_key,
    split_ref,
)
from tegel.core.contracts.capabilities import Capabilities, Principal


def card(**overrides) -> ActionCard:
    data = {
        "id": "xrd.match",
        "version": "0.2.0",
        "title": "Match a structure against a measured pattern",
        "inputs": [
            {"name": "structure", "kind": "structure"},
            {"name": "measured", "kind": "table"},
        ],
        "outputs": [
            {"name": "peaks", "kind": "table"},
            {"name": "d_scale", "kind": "quantity", "unit": "ratio"},
        ],
        "parameters": {
            "type": "object",
            "properties": {"tolerance_deg": {"type": "number", "default": 0.1}},
        },
    }
    data.update(overrides)
    return ActionCard.model_validate(data)


def test_card_defaults_and_ref():
    c = card()
    assert c.ref == "xrd.match@0.2.0"
    assert c.scope == "actions:run"
    assert c.resources == []


@pytest.mark.parametrize("bad", ["XRD.match", ".match", "xrd match", "xrd/match", ""])
def test_action_id_is_lowercase_letters_digits_dot_underscore_dash(bad):
    with pytest.raises(ValidationError):
        card(id=bad)


def test_version_has_no_at_sign():
    with pytest.raises(ValidationError):
        card(version="1@2")


def test_input_and_parameter_names_do_not_overlap():
    with pytest.raises(ValidationError, match="both inputs and parameters"):
        card(parameters={"type": "object", "properties": {"measured": {"type": "string"}}})


def test_duplicate_ports_rejected():
    with pytest.raises(ValidationError, match="duplicate input"):
        card(inputs=[{"name": "s", "kind": "structure"}, {"name": "s", "kind": "table"}])


def test_unit_only_on_quantities_and_no_selection_outputs():
    with pytest.raises(ValidationError, match="not a quantity"):
        card(outputs=[{"name": "peaks", "kind": "table", "unit": "deg"}])
    with pytest.raises(ValidationError, match="cannot be a selection"):
        card(outputs=[{"name": "many", "kind": "selection"}])


def test_parameters_schema_is_an_object():
    with pytest.raises(ValidationError, match="type object"):
        card(parameters={"type": "array"})


def test_catalog_action_holds_the_catalog_writer():
    with pytest.raises(ValidationError, match="catalog-writer"):
        card(id="catalog.ingest", inputs=[], outputs=[], scope="catalog:write")
    ok = card(
        id="catalog.ingest",
        inputs=[],
        outputs=[],
        scope="catalog:write",
        resources=["catalog-writer"],
    )
    assert ok.scope == "catalog:write"


def test_unknown_card_fields_rejected():
    with pytest.raises(ValidationError):
        card(worker="gpu")


def test_check_inputs():
    c = card()
    assert check_inputs(c, {"structure": "urn:a", "measured": "https://x/api/v1/metadata/p"}) == []
    problems = check_inputs(c, {"structure": "", "extra": "u"})
    assert any("unknown inputs ['extra']" in p for p in problems)
    assert any("missing inputs ['measured']" in p for p in problems)
    assert any("'structure' must be a non-empty URI string" in p for p in problems)


def test_selection_inputs_are_lists():
    c = card(inputs=[{"name": "patterns", "kind": "selection"}])
    assert check_inputs(c, {"patterns": ["u1", "u2"]}) == []
    assert check_inputs(c, {"patterns": "u1"})
    assert check_inputs(c, {"patterns": []})


def test_batch_fans_out_over_exactly_one_list():
    c = card()
    req = BatchRequest(action="xrd.match", inputs={"structure": ["a", "b"], "measured": "m"})
    assert batch_port(c, req) == "structure"
    with pytest.raises(ValueError):
        batch_port(
            c, BatchRequest(action="xrd.match", inputs={"structure": ["a"], "measured": ["m"]})
        )
    with pytest.raises(ValueError):
        batch_port(c, BatchRequest(action="xrd.match", inputs={"structure": "a", "measured": "m"}))


def test_split_ref():
    assert split_ref("xrd.match") == ("xrd.match", None)
    assert split_ref("xrd.match@0.2.0") == ("xrd.match", "0.2.0")


def test_run_request_rejects_unknown_fields():
    RunRequest(action="xrd.match", inputs={"structure": "u"}, parameters={"tolerance_deg": 0.2})
    with pytest.raises(ValidationError):
        RunRequest(action="xrd.match", params={})


def test_transitions():
    q, r, s, f, c = (
        RunState.queued,
        RunState.running,
        RunState.succeeded,
        RunState.failed,
        RunState.cancelled,
    )
    assert can_transition(q, r) and can_transition(q, c)
    assert can_transition(r, s) and can_transition(r, f)
    assert not can_transition(r, c)  # a running run is not cancelled
    assert not can_transition(s, f) and not can_transition(c, r) and not can_transition(q, s)


def run(**overrides) -> Run:
    data = {
        "id": "3f2a",
        "action": "xrd.match@0.2.0",
        "state": "queued",
        "principal": "peter",
        "submitted": "2026-10-19T21:00:00Z",
    }
    data.update(overrides)
    return Run.model_validate(data)


def test_run_consistency():
    run()
    run(state="succeeded", ended="2026-10-19T21:01:00Z", result="https://a/resources/3f2a")
    run(state="cancelled")
    with pytest.raises(ValidationError, match="says why"):
        run(state="failed", ended="2026-10-19T21:01:00Z")
    with pytest.raises(ValidationError, match="end time"):
        run(state="succeeded")
    with pytest.raises(ValidationError, match="cached run is succeeded"):
        run(cached=True)
    with pytest.raises(ValidationError, match="only a finished run"):
        run(state="running", result="https://a/resources/3f2a")


def test_request_key_is_stable_and_sensitive():
    c = card()
    k = request_key(c, {"structure": "a", "measured": "m"}, {"tolerance_deg": 0.1})
    assert k == request_key(c, {"measured": "m", "structure": "a"}, {"tolerance_deg": 0.1})
    assert k != request_key(c, {"structure": "a", "measured": "m"}, {"tolerance_deg": 0.2})
    assert k != request_key(
        card(version="0.3.0"), {"structure": "a", "measured": "m"}, {"tolerance_deg": 0.1}
    )


def test_principal_scopes_and_capability_services_are_optional():
    assert Principal(subject="peter").scopes == []
    assert Principal(subject="svc", scopes=["catalog:write"]).scopes == ["catalog:write"]
    assert Capabilities(server_version="1").services == ["read"]
    assert Capabilities(server_version="1", services=["read", "actions"]).services == [
        "read",
        "actions",
    ]
