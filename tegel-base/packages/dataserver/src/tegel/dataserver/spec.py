"""Load the LinkML schema and search profile an incarnation ships."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tegel.core.contracts.types import (
    SearchProfile,
    linkml_attribute_names,
    profile_unknown_slots,
)


def load_spec(profile_path: Path) -> tuple[SearchProfile, dict[str, Any]]:
    """Read a profile and the LinkML document it names.

    A relative ``schema`` path is resolved next to the profile. A slot that is
    not an attribute of the tree-root class is an error.
    """
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Search profile must be a YAML mapping")
    profile = SearchProfile.model_validate(raw)
    schema_path = Path(profile.schema_document)
    if not schema_path.is_absolute():
        schema_path = profile_path.parent / schema_path
    schema = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ValueError("LinkML schema must be a YAML mapping")
    unknown = profile_unknown_slots(profile, set(linkml_attribute_names(schema)))
    if unknown:
        names = ", ".join(unknown)
        raise ValueError(f"Profile slots are not schema attributes: {names}")
    return profile, schema
