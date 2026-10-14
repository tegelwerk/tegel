"""A profile names an external schema under the schema root."""

from pathlib import Path

import pytest

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
