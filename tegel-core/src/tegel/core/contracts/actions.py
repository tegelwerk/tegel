"""Wire contracts for the action server (specs/actionserver.md).

Cards describe actions; runs are executions of them. These models carry no
behavior beyond validation and the request check a server runs before it
queues anything.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ACTION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
DEFAULT_SCOPE = "actions:run"
CATALOG_SCOPE = "catalog:write"
CATALOG_WRITER = "catalog-writer"


class PortKind(StrEnum):
    """What an input or output port carries."""

    structure = "structure"
    trajectory = "trajectory"
    table = "table"
    array = "array"
    quantity = "quantity"
    json = "json"
    file = "file"
    selection = "selection"


class ActionPort(BaseModel):
    """One named input or output of an action."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    kind: PortKind
    description: str = ""
    unit: str | None = None
    optional: bool = False


class ActionCard(BaseModel):
    """Everything a client needs to call an action."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    title: str = ""
    description: str = ""
    inputs: list[ActionPort] = Field(default_factory=list)
    outputs: list[ActionPort] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    scope: str = Field(default=DEFAULT_SCOPE, min_length=1)
    resources: list[str] = Field(default_factory=list)
    validity: str | None = None
    software: list[str] = Field(default_factory=list)
    license: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not ACTION_ID.match(value):
            raise ValueError(f"action id must match {ACTION_ID.pattern}: got {value!r}")
        return value

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        if "@" in value or value != value.strip():
            raise ValueError(
                f"action version must not contain '@' or surrounding space: got {value!r}"
            )
        return value

    @model_validator(mode="after")
    def _consistent(self) -> ActionCard:
        for label, ports in (("input", self.inputs), ("output", self.outputs)):
            names = [p.name for p in ports]
            dupes = sorted({n for n in names if names.count(n) > 1})
            if dupes:
                raise ValueError(f"{self.id}: duplicate {label} ports {dupes}")
        for p in self.outputs:
            if p.kind is PortKind.selection:
                raise ValueError(f"{self.id}: output {p.name!r} cannot be a selection")
            if p.unit and p.kind is not PortKind.quantity:
                raise ValueError(f"{self.id}: output {p.name!r} has a unit but is not a quantity")
        if self.parameters.get("type", "object") != "object":
            raise ValueError(f"{self.id}: parameters must be a JSON Schema of type object")
        clash = sorted({p.name for p in self.inputs} & set(self.parameters.get("properties") or {}))
        if clash:
            raise ValueError(f"{self.id}: names are both inputs and parameters: {clash}")
        if self.scope == CATALOG_SCOPE and CATALOG_WRITER not in self.resources:
            raise ValueError(
                f"{self.id}: a catalog action must hold the {CATALOG_WRITER!r} resource"
            )
        return self

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"


def split_ref(action: str) -> tuple[str, str | None]:
    """Split ``id`` or ``id@version`` into its parts."""
    action_id, sep, version = action.partition("@")
    return action_id, (version if sep else None)


InputValue = str | list[str]


class RunRequest(BaseModel):
    """Body of ``POST /runs``."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    inputs: dict[str, InputValue] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


class BatchRequest(BaseModel):
    """Body of ``POST /batches``: exactly one non-selection input is a list."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    inputs: dict[str, InputValue] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    force: bool = False


class RunState(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


FINAL_STATES = frozenset({RunState.succeeded, RunState.failed, RunState.cancelled})
TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.queued: frozenset({RunState.running, RunState.cancelled}),
    RunState.running: frozenset({RunState.succeeded, RunState.failed}),
    RunState.succeeded: frozenset(),
    RunState.failed: frozenset(),
    RunState.cancelled: frozenset(),
}


def can_transition(old: RunState, new: RunState) -> bool:
    """Whether a run may move from ``old`` to ``new``."""
    return new in TRANSITIONS[old]


class Run(BaseModel):
    """One execution of one action version, as ``GET /runs/{run}`` returns it."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    state: RunState
    inputs: dict[str, InputValue] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    principal: str = Field(min_length=1)
    batch: str | None = None
    submitted: str
    started: str | None = None
    ended: str | None = None
    cached: bool = False
    result: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Run:
        if self.cached and self.state is not RunState.succeeded:
            raise ValueError("a cached run is succeeded")
        if self.state in FINAL_STATES and self.state is not RunState.cancelled and not self.ended:
            raise ValueError(f"a {self.state.value} run has an end time")
        if self.state is RunState.failed and not self.error:
            raise ValueError("a failed run says why")
        if self.result and self.state not in FINAL_STATES:
            raise ValueError("only a finished run has a result")
        return self


class Batch(BaseModel):
    """Response of ``POST /batches``."""

    model_config = ConfigDict(extra="ignore")

    batch: str = Field(min_length=1)
    action: str = Field(min_length=1)
    runs: list[Run]


def check_inputs(card: ActionCard, inputs: dict[str, Any]) -> list[str]:
    """Problems with ``inputs`` against the card; empty when the inputs are acceptable.

    Parameters are checked against ``card.parameters`` by the server with a JSON
    Schema validator; tegel-core does not carry one.
    """
    problems: list[str] = []
    ports = {p.name: p for p in card.inputs}
    unknown = sorted(set(inputs) - set(ports))
    if unknown:
        problems.append(f"unknown inputs {unknown}; inputs are {sorted(ports)}")
    missing = [p.name for p in card.inputs if not p.optional and p.name not in inputs]
    if missing:
        problems.append(f"missing inputs {missing}")
    for name, value in inputs.items():
        port = ports.get(name)
        if port is None:
            continue
        if port.kind is PortKind.selection:
            if not (
                isinstance(value, list)
                and value
                and all(isinstance(v, str) and v.strip() for v in value)
            ):
                problems.append(f"input {name!r} is a selection: a non-empty list of URIs")
        elif not (isinstance(value, str) and value.strip()):
            problems.append(f"input {name!r} must be a non-empty URI string")
    return problems


def batch_port(card: ActionCard, request: BatchRequest) -> str:
    """The one input a batch fans out over; raises ``ValueError`` otherwise."""
    lists = [
        name
        for name, value in request.inputs.items()
        if isinstance(value, list)
        and not any(p.name == name and p.kind is PortKind.selection for p in card.inputs)
    ]
    if len(lists) != 1:
        raise ValueError("a batch gives exactly one non-selection input as a list of URIs")
    if not request.inputs[lists[0]]:
        raise ValueError(f"input {lists[0]!r} is an empty list")
    return lists[0]


def request_key(card: ActionCard, inputs: dict[str, Any], parameters: dict[str, Any]) -> str:
    """Hash of the action ref, input URIs and validated parameters, used to reuse results."""
    payload = json.dumps(
        {"action": card.ref, "inputs": inputs, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
