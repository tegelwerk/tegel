"""Plan and register a URI as a node on a Tiled server."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from tegel.register._uriparse import split_uri
from tegel.register.context import BindContext
from tegel.register.errors import (
    BindError,
    ModeNotAvailableError,
    NotReadableError,
    SiteConfigError,
    UnknownSchemeError,
    URIResolverError,
)
from tegel.register.redaction import is_opaque_scheme, redact_uri
from tegel.register.tiled.origin import ORIGIN_SPEC, origin_metadata

__version_source__ = None


class Mode(str, Enum):
    """Bind modes, in auto order: EXISTING, REFERENCE, PROXY, ACQUIRE."""

    EXISTING = "existing"
    REFERENCE = "reference"
    PROXY = "proxy"
    ACQUIRE = "acquire"


class OnConflict(str, Enum):
    RETURN = "return"
    ERROR = "error"
    REPLACE = "replace"


_PREFERENCE = (Mode.EXISTING, Mode.REFERENCE, Mode.PROXY, Mode.ACQUIRE)


@dataclass(frozen=True)
class Origin:
    uri: str
    resolved_uri: str
    trail: tuple[str, ...]
    protocol: str
    mode: Mode
    binder_version: str
    acquired_from: str | None = None


@dataclass(frozen=True)
class NodeSpec:
    """Everything needed to call Container.new. Pure data."""

    key: str
    structure_family: str
    data_sources: tuple[dict, ...]
    metadata: dict
    specs: tuple[dict, ...]
    children: tuple[NodeSpec, ...] = ()


@dataclass(frozen=True)
class Acquisition:
    """Work to do before the node can exist."""

    protocol: str
    resolved_uri: str
    landing_local: str
    recursive: bool
    expected_size: int | None = None
    expected_md5: str | None = None


@dataclass(frozen=True)
class BinderPlan:
    """Binder-owned fields of a plan. ``bind.plan`` adds origin and metadata."""

    node: NodeSpec | None = None
    existing_path: str | None = None
    acquisition: Acquisition | None = None
    notes: tuple[str, ...] = ()
    size_bytes: int | None = None


@dataclass(frozen=True)
class Plan:
    origin: Origin
    target: str
    node: NodeSpec | None
    existing_path: str | None
    acquisition: Acquisition | None
    notes: tuple[str, ...] = ()
    size_bytes: int | None = None
    """Bytes the registered node holds, when the binder can tell cheaply; ``None`` when unknown."""
    moves_bytes: bool = False
    """True when executing the plan copies bytes (ACQUIRE). PROXY reads on demand instead."""

    def to_json(self) -> str:
        """Stable JSON. Secret values are not part of a plan."""
        return json.dumps(_plain(asdict(self)), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Binding:
    origin: Origin
    node: Any
    path: str
    created: bool
    acquired_path: str | None = None


def plan(
    uri: str,
    into: Any,
    *,
    key: str | None = None,
    mode: Mode | str = "auto",
    metadata: Mapping | None = None,
    context: BindContext | None = None,
) -> Plan:
    """Describe a bind. May read remote metadata. Never writes."""
    ctx = context or BindContext.default()
    resolved, trail, source, binder, chosen, notes = _prepare(
        uri, into, ctx, key=key, mode=mode
    )
    binder_plan = binder.plan(resolved, source, into, ctx, mode=chosen, key=key)
    origin = _origin(
        ctx, uri=uri, resolved=resolved, trail=trail, protocol=binder.protocol, mode=chosen
    )
    node = _merge_metadata(binder_plan.node, origin, metadata)
    extra = tuple(binder_plan.notes)
    return Plan(
        origin=origin,
        target=_target_label(into),
        node=node,
        existing_path=binder_plan.existing_path,
        acquisition=binder_plan.acquisition,
        notes=notes + extra,
        size_bytes=_plan_size(binder_plan),
        moves_bytes=binder_plan.acquisition is not None,
    )


def _plan_size(binder_plan: BinderPlan) -> int | None:
    if binder_plan.size_bytes is not None:
        return binder_plan.size_bytes
    if binder_plan.acquisition is not None:
        return binder_plan.acquisition.expected_size
    return None


def register(
    uri: str,
    into: Any,
    *,
    key: str | None = None,
    mode: Mode | str = "auto",
    metadata: Mapping | None = None,
    on_conflict: OnConflict | str = "return",
    acquire_timeout: float | None = None,
    context: BindContext | None = None,
) -> Binding:
    """Execute a plan against ``into``."""
    planned = plan(
        uri, into, key=key, mode=mode, metadata=metadata, context=context
    )
    ctx = context or BindContext.default()
    conflict = on_conflict if isinstance(on_conflict, OnConflict) else OnConflict(on_conflict)
    if planned.existing_path:
        return Binding(
            origin=planned.origin,
            node=_node_at(into, planned.existing_path),
            path=planned.existing_path,
            created=False,
            acquired_path=None,
        )
    node_spec = planned.node
    acquired_path = None
    origin = planned.origin
    if planned.acquisition is not None:
        binder = ctx.binders.get(planned.acquisition.protocol)  # type: ignore[union-attr]
        local = binder.acquire(planned.acquisition, ctx, timeout=acquire_timeout)
        acquired_path = str(local)
        node_spec, origin = _replan_acquired(
            ctx, into, planned, local, key=key, metadata=metadata
        )
    if node_spec is None:
        raise BindError("plan produced no node to create")
    from tegel.register.tiled.apply import create

    node, created, path = create(into, node_spec, on_conflict=conflict, origin=origin)
    return Binding(
        origin=origin, node=node, path=path, created=created, acquired_path=acquired_path
    )


def _prepare(uri, into, ctx: BindContext, *, key, mode):
    if ctx.closed:
        raise URIResolverError("BindContext is closed")
    if ctx.site is None or ctx.binders is None:
        raise URIResolverError("BindContext is missing site or binders")
    resolved, trail = ctx.resolve_chain(uri)
    source = ctx.site.source_for(resolved)
    scheme = split_uri(resolved).scheme
    protocol = source.protocol if source is not None else scheme
    try:
        binder = ctx.binders.get(protocol)
    except UnknownSchemeError as exc:
        raise UnknownSchemeError(
            f"no source and no binder for scheme {scheme!r}"
        ) from exc
    if source is not None:
        binder.validate_source(source)
    feasible = list(binder.feasible_modes(resolved, source, into, ctx))
    chosen, notes = _choose_mode(feasible, mode)
    return resolved, trail, source, binder, chosen, notes


def _choose_mode(feasible: list, requested: Mode | str) -> tuple[Mode, tuple[str, ...]]:
    by_mode: dict[Mode, str | None] = {}
    for item in feasible:
        mode, reason = item
        if not isinstance(mode, Mode):
            mode = Mode(mode)
        by_mode[mode] = reason
    reasons = {mode: reason for mode, reason in by_mode.items() if reason}
    feasible_modes = [mode for mode, reason in by_mode.items() if reason is None]
    notes = tuple(f"{mode.value}: {reason}" for mode, reason in sorted(reasons.items(), key=lambda pair: pair[0].value))
    if requested == "auto":
        for mode in _PREFERENCE:
            if by_mode.get(mode) is None and mode in by_mode:
                return mode, notes
        raise ModeNotAvailableError(
            "no mode is feasible: " + ("; ".join(notes) or "binder reported none"),
            feasible=feasible_modes,
            reasons=reasons,
        )
    mode = requested if isinstance(requested, Mode) else Mode(requested)
    reason = by_mode.get(mode, "not reported by the binder")
    if reason is not None:
        raise ModeNotAvailableError(
            f"mode {mode.value!r} is not feasible: {reason}",
            feasible=feasible_modes,
            reasons=reasons,
        )
    return mode, notes


def _origin(ctx: BindContext, *, uri: str, resolved: str, trail: tuple[str, ...], protocol: str, mode: Mode, acquired_from: str | None = None) -> Origin:
    import tegel.register

    return Origin(
        uri=_redact_if_opaque(ctx, uri),
        resolved_uri=resolved,
        trail=tuple(_redact_if_opaque(ctx, hop) for hop in trail),
        protocol=protocol,
        mode=mode,
        binder_version=tegel.register.__version__,
        acquired_from=acquired_from,
    )


def _redact_if_opaque(ctx: BindContext, uri: str) -> str:
    try:
        scheme = split_uri(uri).scheme
    except ValueError:
        return uri
    opaque = is_opaque_scheme(scheme)
    if not opaque and ctx.namespaces is not None and scheme in ctx.namespaces.resolvers:
        opaque = bool(getattr(ctx.namespaces.resolvers[scheme], "opaque_payload", False))
    if opaque:
        return redact_uri(uri, opaque=True)
    return uri


def _merge_metadata(node: NodeSpec | None, origin: Origin, caller: Mapping | None) -> NodeSpec | None:
    if caller is not None and "tegel_register" in caller:
        raise ValueError("metadata key 'tegel_register' is reserved")
    if node is None:
        return None
    metadata = dict(caller or {})
    metadata.update(node.metadata)
    metadata["tegel_register"] = origin_metadata(origin)
    specs = tuple(dict(spec) for spec in node.specs)
    if ORIGIN_SPEC not in [dict(spec) for spec in specs]:
        specs = specs + (dict(ORIGIN_SPEC),)
    children = tuple(
        merged
        for child in node.children
        if (merged := _merge_metadata(child, origin, None)) is not None
    )
    return NodeSpec(
        key=node.key,
        structure_family=node.structure_family,
        data_sources=node.data_sources,
        metadata=metadata,
        specs=specs,
        children=children,
    )


def _replan_acquired(ctx, into, planned: Plan, local, *, key, metadata):
    from pathlib import Path

    file_binder = ctx.binders.get("file")
    file_uri = Path(local).resolve().as_uri()
    try:
        binder_plan = file_binder.plan(
            file_uri, None, into, ctx, mode=Mode.REFERENCE, key=key
        )
    except (ModeNotAvailableError, NotReadableError) as exc:
        roots = [str(entry.local) for entry in ctx.site.readable]
        raise SiteConfigError(
            f"landing path {local} is not under a readable entry: {roots}"
        ) from exc
    if binder_plan.node is None:
        roots = [str(entry.local) for entry in ctx.site.readable]
        raise SiteConfigError(
            f"landing path {local} is not under a readable entry: {roots}"
        )
    origin = Origin(
        uri=planned.origin.uri,
        resolved_uri=planned.origin.resolved_uri,
        trail=planned.origin.trail,
        protocol=planned.origin.protocol,
        mode=Mode.ACQUIRE,
        binder_version=planned.origin.binder_version,
        acquired_from=planned.origin.resolved_uri,
    )
    node = _merge_metadata(binder_plan.node, origin, metadata)
    return node, origin


def _target_label(into: Any) -> str:
    for attr in ("uri", "item"):
        value = getattr(into, attr, None)
        if isinstance(value, str) and value:
            return value
    api = getattr(getattr(into, "context", None), "api_uri", None)
    if isinstance(api, str):
        return api
    return ""


def _node_at(into: Any, path: str) -> Any:
    """Walk ``path`` from the server root. ``into`` may already be a nested node."""
    node = into
    for _ in range(64):
        parts = [part for part in (getattr(node, "path_parts", None) or []) if part]
        if not parts:
            break
        parent = getattr(node, "parent", None)
        if parent is None or parent is node:
            break
        node = parent
    for part in path.strip("/").split("/"):
        if part:
            node = node[part]
    return node


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
