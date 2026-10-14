"""A profile names an external schema under the schema root."""

from pathlib import Path

import pytest
import yaml

from tegel.core.contracts.types import linkml_attribute_names
from tegel.core.spec import load_spec, record_slots

SCHEMA = """\
id: https://example.com/series
name: series
version: "2"
imports:
  - linkml:types
slots:
  uuid:
    range: string
  sample_name:
    range: string
  recorded:
    range: datetime
classes:
  Base:
    slots:
      - uuid
      - sample_name
    attributes:
      plugin_id:
        range: string
      source_name:
        range: string
  Series:
    is_a: Base
    slots:
      - recorded
    attributes:
      kind:
        range: string
"""

PROFILE = """\
id: https://example.com/profile
name: series search
schema_id: {schema_id}
schema_version: "{schema_version}"
schema_path: {schema_path}
class: {class_name}
search: [kind]
properties: [recorded]
entity:
  name: sample_name
  identifier: uuid
  dateCreated: recorded
"""


def _write(
    tmp_path: Path,
    *,
    schema_id: str = "https://example.com/series",
    schema_version: str = "2",
    schema_path: str = "series/schema.yaml",
    class_name: str = "Series",
) -> tuple[Path, Path]:
    root = tmp_path / "schemas"
    (root / "series").mkdir(parents=True)
    (root / "series" / "schema.yaml").write_text(SCHEMA, encoding="utf-8")
    profile = tmp_path / "incarnation" / "profile.yaml"
    profile.parent.mkdir()
    profile.write_text(
        PROFILE.format(
            schema_id=schema_id,
            schema_version=schema_version,
            schema_path=schema_path,
            class_name=class_name,
        ),
        encoding="utf-8",
    )
    return profile, root


def test_slots_style_schema_with_is_a_loads(tmp_path: Path) -> None:
    profile_path, root = _write(tmp_path)
    profile, schema = load_spec(profile_path, root)
    assert profile.class_name == "Series"
    assert record_slots(profile, schema) == {
        "uuid",
        "sample_name",
        "plugin_id",
        "source_name",
        "recorded",
        "kind",
    }


def test_absolute_schema_path_is_rejected(tmp_path: Path) -> None:
    absolute = str((tmp_path / "schemas" / "series" / "schema.yaml").resolve())
    profile_path, root = _write(tmp_path, schema_path=absolute)
    with pytest.raises(ValueError, match="relative"):
        load_spec(profile_path, root)


def test_schema_path_outside_the_root_is_rejected(tmp_path: Path) -> None:
    profile_path, root = _write(tmp_path, schema_path="../incarnation/profile.yaml")
    with pytest.raises(ValueError, match="leaves the schema root"):
        load_spec(profile_path, root)


def test_mismatched_id_or_version_is_rejected(tmp_path: Path) -> None:
    profile_path, root = _write(tmp_path, schema_id="https://example.com/other")
    with pytest.raises(ValueError, match="schema id"):
        load_spec(profile_path, root)
    profile_path.write_text(
        PROFILE.format(
            schema_id="https://example.com/series",
            schema_version="3",
            schema_path="series/schema.yaml",
            class_name="Series",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema version"):
        load_spec(profile_path, root)


def test_missing_class_is_rejected(tmp_path: Path) -> None:
    profile_path, root = _write(tmp_path, class_name="Pin")
    with pytest.raises(ValueError, match="Pin"):
        load_spec(profile_path, root)


MIXIN_SCHEMA = {
    "id": "https://example.com/mixed",
    "version": "1",
    "slots": {"m1": {"range": "string"}, "own": {"range": "string"}},
    "classes": {
        "M": {"mixin": True, "slots": ["m1"], "attributes": {"m2": {"range": "string"}}},
        "Record": {"mixins": ["M"], "slots": ["own"]},
    },
}


def test_mixin_slots_are_class_slots(tmp_path: Path) -> None:
    assert set(linkml_attribute_names(MIXIN_SCHEMA, "Record")) == {"m1", "m2", "own"}
    root = tmp_path / "schemas"
    root.mkdir()
    (root / "mixed.yaml").write_text(yaml.safe_dump(MIXIN_SCHEMA), encoding="utf-8")
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        yaml.safe_dump(
            {
                "id": "https://example.com/profile",
                "name": "mixed",
                "schema_id": "https://example.com/mixed",
                "schema_version": "1",
                "schema_path": "mixed.yaml",
                "class": "Record",
                "search": ["m1"],
                "properties": ["m2"],
                "entity": {"name": "own", "identifier": "own", "dateCreated": "m2"},
            }
        ),
        encoding="utf-8",
    )
    loaded, schema = load_spec(profile, root)
    assert "m1" in record_slots(loaded, schema)


def test_mixin_of_ancestor_is_followed() -> None:
    schema = {
        "classes": {
            "M": {"attributes": {"from_mixin": {}}},
            "B": {"mixins": ["M"], "attributes": {"b": {}}},
            "A": {"is_a": "B", "attributes": {"a": {}}},
        }
    }
    assert linkml_attribute_names(schema, "A") == ["from_mixin", "b", "a"]


def test_mixin_cycle_raises() -> None:
    schema = {
        "classes": {
            "M1": {"mixins": ["M2"], "attributes": {"x": {}}},
            "M2": {"mixins": ["M1"], "attributes": {"y": {}}},
        }
    }
    with pytest.raises(ValueError, match="cycle at M1"):
        linkml_attribute_names(schema, "M1")


def test_missing_mixin_raises() -> None:
    schema = {"classes": {"A": {"mixins": ["Nope"], "attributes": {"x": {}}}}}
    with pytest.raises(ValueError, match="Nope"):
        linkml_attribute_names(schema, "A")


def test_undefined_listed_slot_raises() -> None:
    schema = {"slots": {"real": {}}, "classes": {"A": {"slots": ["real", "ghost"]}}}
    with pytest.raises(ValueError, match="slot ghost on class A is not defined in the schema"):
        linkml_attribute_names(schema, "A")


def _versions(tmp_path: Path, schema_version: str, profile_version: str) -> tuple[Path, Path]:
    profile_path, root = _write(tmp_path)
    schema_file = root / "series" / "schema.yaml"
    schema_file.write_text(
        SCHEMA.replace('version: "2"', f"version: {schema_version}"), encoding="utf-8"
    )
    profile_path.write_text(
        PROFILE.format(
            schema_id="https://example.com/series",
            schema_version="X",
            schema_path="series/schema.yaml",
            class_name="Series",
        ).replace('schema_version: "X"', f"schema_version: {profile_version}"),
        encoding="utf-8",
    )
    return profile_path, root


def test_unquoted_schema_version_raises(tmp_path: Path) -> None:
    profile_path, root = _versions(tmp_path, "1.10", '"1.10"')
    with pytest.raises(ValueError, match="schema version must be a quoted string in .*float 1.1"):
        load_spec(profile_path, root)


def test_unquoted_profile_version_raises(tmp_path: Path) -> None:
    profile_path, root = _versions(tmp_path, '"1.10"', "1.10")
    with pytest.raises(
        ValueError, match="schema_version must be a quoted string in the profile: got float 1.1"
    ):
        load_spec(profile_path, root)


def test_quoted_versions_match(tmp_path: Path) -> None:
    profile_path, root = _versions(tmp_path, '"1.10"', '"1.10"')
    profile, _schema = load_spec(profile_path, root)
    assert profile.schema_version == "1.10"
