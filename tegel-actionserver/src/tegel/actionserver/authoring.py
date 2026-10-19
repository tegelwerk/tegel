"""Write an action as a decorated function.

The contract (specs/actionserver.md, Registration) asks for an object with a
``card`` and a ``run(context, inputs, parameters)`` method. ``@action`` builds
that object from a function::

    @action("array.stats", version="0.1.0",
            inputs=[port("data", "array")],
            outputs=[port("mean", "quantity"), port("summary", "json")])
    def array_stats(ctx, data, *, axis: int | None = None):
        return {"mean": Quantity(float(data.mean())), "summary": {...}}

Positional arguments after the context are the input ports, in order.
Keyword-only arguments are the parameters; their annotations and defaults
become the card's parameter schema. The function returns outputs by port name.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, create_model

from tegel.core.contracts.actions import ActionCard, ActionPort, PortKind


def port(name: str, kind: str | PortKind, **kwargs: Any) -> ActionPort:
    """Shorthand for ``ActionPort(name=..., kind=...)``."""
    return ActionPort(name=name, kind=PortKind(kind), **kwargs)


@dataclass
class Quantity:
    """A number with its unit, uncertainty, the method that produced it, and its conditions."""

    value: float
    unit: str | None = None
    uncertainty: float | None = None
    method: str | None = None
    conditions: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        found: dict[str, Any] = {"numeric_value": float(self.value), "unit": self.unit}
        if self.uncertainty is not None:
            found["uncertainty"] = float(self.uncertainty)
        if self.method:
            found["method"] = self.method
        if self.conditions:
            found["conditions"] = self.conditions
        return found


class Action:
    """A registered action: its card and the function that runs it."""

    def __init__(
        self, card: ActionCard, fn: Callable[..., dict[str, Any]], params: type[BaseModel]
    ) -> None:
        self.card = card
        self._fn = fn
        self._params = params
        self.__doc__ = fn.__doc__
        self.__name__ = getattr(fn, "__name__", card.id)

    def run(
        self, context: Any, inputs: dict[str, Any], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        checked = self._params.model_validate(parameters).model_dump()
        ordered = [inputs.get(p.name) for p in self.card.inputs]
        return self._fn(context, *ordered, **checked)

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self._fn(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<Action {self.card.ref}>"


def action(
    id: str,
    *,
    version: str,
    inputs: Sequence[ActionPort] = (),
    outputs: Sequence[ActionPort] = (),
    scope: str | None = None,
    resources: Sequence[str] = (),
    software: Sequence[str] = (),
    license: str | None = None,
    validity: str | None = None,
    title: str | None = None,
) -> Callable[[Callable[..., dict[str, Any]]], Action]:
    """Turn a function into an ``Action``. See the module docstring for the signature rules."""

    def wrap(fn: Callable[..., dict[str, Any]]) -> Action:
        signature = list(inspect.signature(fn).parameters.values())
        if not signature:
            raise TypeError(f"action {id!r}: the first argument is the run context")
        positional = [p.name for p in signature[1:] if p.kind is not inspect.Parameter.KEYWORD_ONLY]
        declared = [p.name for p in inputs]
        if positional != declared:
            raise TypeError(
                f"action {id!r}: positional arguments {positional} "
                f"must match input ports {declared}"
            )
        params = _parameter_model(fn, id)
        schema = params.model_json_schema()
        schema.pop("title", None)
        doc = inspect.getdoc(fn) or ""
        extra: dict[str, Any] = {}
        if scope:
            extra["scope"] = scope
        card = ActionCard(
            id=id,
            version=version,
            title=title or (doc.splitlines()[0] if doc else id),
            description=doc,
            inputs=list(inputs),
            outputs=list(outputs),
            parameters=schema,
            resources=list(resources),
            software=list(software),
            license=license,
            validity=validity,
            **extra,
        )
        return Action(card, fn, params)

    return wrap


def _parameter_model(fn: Callable[..., Any], action_id: str) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    for p in inspect.signature(fn, eval_str=True).parameters.values():
        if p.kind is not inspect.Parameter.KEYWORD_ONLY:
            continue
        annotation = p.annotation if p.annotation is not inspect.Parameter.empty else Any
        default = p.default if p.default is not inspect.Parameter.empty else ...
        fields[p.name] = (annotation, default)
    safe = "".join(ch if ch.isalnum() else "_" for ch in action_id)
    return create_model(f"Parameters_{safe}", __config__={"extra": "forbid"}, **fields)
