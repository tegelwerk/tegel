"""Create Tiled nodes from a NodeSpec."""
from __future__ import annotations

from typing import Any

from tegel.register.bind import NodeSpec, OnConflict, Origin
from tegel.register.errors import KeyConflictError


def create(into: Any, node_spec: NodeSpec, *, on_conflict: OnConflict, origin: Origin) -> tuple[Any, bool, str]:
    """Create ``node_spec`` under ``into``. Return the node, whether it was created, and its path."""
    existing = _existing(into, node_spec.key)
    if existing is None:
        created_node = _create_new(into, node_spec)
        for child in node_spec.children:
            create(created_node, child, on_conflict=on_conflict, origin=origin)
        return created_node, True, _path(created_node, into, node_spec.key)
    if on_conflict is OnConflict.ERROR:
        raise _conflict(node_spec.key, existing, origin)
    if on_conflict is OnConflict.REPLACE:
        existing.delete(recursive=True, external_only=True)
        return create(into, node_spec, on_conflict=OnConflict.ERROR, origin=origin)
    if _same_origin(existing, origin):
        return existing, False, _path(existing, into, node_spec.key)
    raise _conflict(node_spec.key, existing, origin)


def _existing(into: Any, key: str) -> Any | None:
    """Return the child named ``key``, or None. Tiled raises KeyError when it is absent."""
    try:
        return into[key]
    except KeyError:
        return None


def _create_new(into: Any, node_spec: NodeSpec) -> Any:
    from tiled.structures.core import Spec

    specs = [Spec(spec["name"], spec.get("version")) for spec in node_spec.specs]
    return into.new(
        node_spec.structure_family,
        [data_source(source) for source in node_spec.data_sources],
        key=node_spec.key,
        metadata=node_spec.metadata,
        specs=specs,
    )


def data_source(raw: dict) -> Any:
    """A Tiled ``DataSource`` from the dict form ``describe_local`` returns."""
    from tiled.structures.core import StructureFamily
    from tiled.structures.data_source import Asset, DataSource, Management

    assets = [
        Asset(
            data_uri=asset["data_uri"],
            is_directory=bool(asset.get("is_directory")),
            parameter=asset.get("parameter"),
            num=asset.get("num"),
            id=asset.get("id"),
            size=asset.get("size"),
        )
        for asset in raw.get("assets") or []
    ]
    family = raw["structure_family"]
    family_value = family.value if hasattr(family, "value") else family
    management = raw.get("management") or "external"
    management_value = management.value if hasattr(management, "value") else management
    return DataSource(
        structure_family=StructureFamily(family_value),
        structure=raw.get("structure"),
        mimetype=raw.get("mimetype"),
        parameters=dict(raw.get("parameters") or {}),
        properties=dict(raw.get("properties") or {}),
        assets=assets,
        management=Management(management_value),
    )


def _same_origin(existing: Any, origin: Origin) -> bool:
    recorded = dict(existing.metadata).get("tegel_register") or {}
    if not isinstance(recorded, dict):
        return False
    return recorded.get("origin") == origin.uri or recorded.get("resolved") == origin.resolved_uri


def _conflict(key: str, existing: Any, origin: Origin) -> KeyConflictError:
    recorded = dict(existing.metadata).get("tegel_register") or {}
    previous = recorded.get("origin") if isinstance(recorded, dict) else None
    return KeyConflictError(
        f"key {key!r} exists for origin {previous!r}; this URI is {origin.uri!r}"
    )


def _path(node: Any, into: Any, key: str) -> str:
    parts = [str(part) for part in (getattr(node, "path_parts", None) or []) if part]
    if parts:
        return "/" + "/".join(parts)
    parent = [str(part) for part in (getattr(into, "path_parts", None) or []) if part]
    return "/" + "/".join([*parent, key])
