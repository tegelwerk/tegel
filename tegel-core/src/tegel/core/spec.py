"""Load a search profile and the external LinkML schema it names."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from tegel.core.contracts.types import (
    SearchProfile,
    linkml_attribute_names,
    profile_unknown_slots,
)

SCHEMA_ROOT_ENV = "TEGEL_SCHEMA_ROOT"


def schema_root_from_env() -> Path:
    """Return ``TEGEL_SCHEMA_ROOT``. It is required."""
    value = os.environ.get(SCHEMA_ROOT_ENV, "").strip()
    if not value:
        raise ValueError(f"{SCHEMA_ROOT_ENV} is required")
    return Path(value)


def resolve_schema_path(schema_root: Path, schema_path: str) -> Path:
    """Return ``schema_root / schema_path``. It must stay inside the root."""
    relative = Path(schema_path)
    if relative.is_absolute():
        raise ValueError(f"schema_path must be relative to the schema root: {schema_path}")
    root = Path(schema_root).resolve()
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"schema_path leaves the schema root: {schema_path}")
    return resolved


def load_spec(
    profile_path: Path, schema_root: Path
) -> tuple[SearchProfile, dict[str, Any]]:
    """Read a profile and the LinkML document it names under ``schema_root``.

    The document's ``id`` and ``version`` must equal the profile's
    ``schema_id`` and ``schema_version``. Every profile field must be a slot of
    the profile's class. Nothing is fetched over the network.
    """
    raw = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Search profile must be a YAML mapping")
    profile = SearchProfile.model_validate(raw)
    path = resolve_schema_path(schema_root, profile.schema_path)
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ValueError("LinkML schema must be a YAML mapping")
    if schema.get("id") != profile.schema_id:
        raise ValueError(
            f"schema id {schema.get('id')!r} does not match schema_id {profile.schema_id!r}"
        )
    version = schema.get("version")
    if version is not None and not isinstance(version, str):
        raise ValueError(
            f"schema version must be a quoted string in {path}: "
            f"got {type(version).__name__} {version!r}"
        )
    if version != profile.schema_version:
        raise ValueError(
            f"schema version {version!r} does not match schema_version "
            f"{profile.schema_version!r}"
        )
    slots = set(linkml_attribute_names(schema, profile.class_name))
    unknown = profile_unknown_slots(profile, slots)
    if unknown:
        names = ", ".join(unknown)
        raise ValueError(f"Profile fields are not slots of {profile.class_name}: {names}")
    return profile, schema


def record_slots(profile: SearchProfile, schema: dict[str, Any]) -> frozenset[str]:
    """Return the slot names a record of the profile's class may carry."""
    return frozenset(linkml_attribute_names(schema, profile.class_name))
