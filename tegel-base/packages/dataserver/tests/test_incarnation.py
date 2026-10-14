"""Static incarnation checks. The toy package is a filesystem path, not an import."""

from pathlib import Path

import pytest

from tegel.dataserver.testing.incarnation import (
    assert_incarnation_profile,
    assert_incarnation_project,
)
from tegel.dataserver.testing.layout import assert_incarnation_layout

TOY = Path(__file__).resolve().parents[3] / "examples" / "toy"

_PROJECT = """\
[project]
name = "sample"
version = "0.1.0"
dependencies = [{dependencies}]

[project.entry-points."tegel.resource_types"]
sample = "tegel.sample.dataserver:RECORD"

[project.entry-points."tegel.extractors"]
sample = "tegel.sample.dataserver:Sample"

[project.entry-points."tegel.readers"]
sample = "tegel.sample.dataserver:Sample"
"""

_RECORD = """\
id: https://w3id.org/tegel/sample/record
name: sample
classes:
  Record:
    tree_root: true
    attributes:
      sample_name:
        range: string
      uuid:
        range: string
      recorded:
        range: {recorded_range}
      channel:
        range: string
        multivalued: {multivalued}
"""

_PROFILE = """\
id: https://w3id.org/tegel/sample/profile
name: sample
schema: record.yaml
search:
  - {search}
properties: []
entity:
  name: sample_name
  identifier: uuid
  dateCreated: recorded
"""


def test_toy_package_matches_the_static_contract() -> None:
    assert_incarnation_layout(TOY)
    assert_incarnation_project(TOY)
    assert_incarnation_profile(TOY)


def test_parent_init_is_rejected(tmp_path: Path) -> None:
    _module(tmp_path, "x = 1\n")
    (tmp_path / "src" / "tegel" / "__init__.py").write_text("", encoding="utf-8")
    with pytest.raises(AssertionError, match="src/tegel/__init__.py"):
        assert_incarnation_layout(tmp_path)


def test_module_beside_dataserver_is_rejected(tmp_path: Path) -> None:
    _module(tmp_path, "x = 1\n")
    extra = tmp_path / "src" / "tegel" / "sample" / "etl.py"
    extra.write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="etl.py"):
        assert_incarnation_layout(tmp_path)


def test_tree_walk_is_rejected(tmp_path: Path) -> None:
    _module(tmp_path, "from pathlib import Path\nPath('.').rglob('*')\n")
    with pytest.raises(AssertionError, match="walks a tree"):
        assert_incarnation_layout(tmp_path)


def test_third_runtime_dependency_is_rejected(tmp_path: Path) -> None:
    _write_project(tmp_path, '"tegel-core", "h5py", "numpy"')
    with pytest.raises(AssertionError, match="format library"):
        assert_incarnation_project(tmp_path)


def test_project_scripts_are_rejected(tmp_path: Path) -> None:
    text = _PROJECT.format(dependencies='"tegel-core"')
    text += '\n[project.scripts]\nsample = "tegel.sample.dataserver:main"\n'
    (tmp_path / "pyproject.toml").write_text(text, encoding="utf-8")
    with pytest.raises(AssertionError, match="project.scripts"):
        assert_incarnation_project(tmp_path)


def test_auth_entry_point_is_rejected(tmp_path: Path) -> None:
    text = _PROJECT.format(dependencies='"tegel-core"')
    text += '\n[project.entry-points."tegel.auth"]\nlocal = "tegel.sample.dataserver:Auth"\n'
    (tmp_path / "pyproject.toml").write_text(text, encoding="utf-8")
    with pytest.raises(AssertionError, match="tegel.auth"):
        assert_incarnation_project(tmp_path)


def test_temporal_search_field_is_rejected(tmp_path: Path) -> None:
    _write_schema(tmp_path, search="recorded", recorded_range="datetime", multivalued="false")
    with pytest.raises(AssertionError, match="not categorical"):
        assert_incarnation_profile(tmp_path)


def test_multivalued_search_field_is_rejected(tmp_path: Path) -> None:
    _write_schema(tmp_path, search="channel", recorded_range="string", multivalued="true")
    with pytest.raises(AssertionError, match="multivalued"):
        assert_incarnation_profile(tmp_path)


def _module(root: Path, source: str) -> None:
    path = root / "src" / "tegel" / "sample" / "dataserver" / "__init__.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")


def _write_project(root: Path, dependencies: str) -> None:
    text = _PROJECT.format(dependencies=dependencies)
    (root / "pyproject.toml").write_text(text, encoding="utf-8")


def _write_schema(root: Path, *, search: str, recorded_range: str, multivalued: str) -> None:
    schema = root / "schema"
    schema.mkdir()
    (schema / "record.yaml").write_text(
        _RECORD.format(recorded_range=recorded_range, multivalued=multivalued),
        encoding="utf-8",
    )
    (schema / "profile.yaml").write_text(_PROFILE.format(search=search), encoding="utf-8")
