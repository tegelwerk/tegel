"""Static incarnation checks. The toy package is a filesystem path, not an import."""

import shutil
from pathlib import Path

import httpx
import pytest

from tegel.dataserver.testing.incarnation import (
    assert_incarnation,
    assert_incarnation_profile,
    assert_incarnation_project,
    assert_incarnation_ships_no_schema,
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
version: "1"
classes:
  Record:
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
schema_id: https://w3id.org/tegel/sample/record
schema_version: "1"
schema_path: {schema_path}
class: Record
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
    assert_incarnation_ships_no_schema(TOY)
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
    package, schemas = _write_schema(
        tmp_path, search="recorded", recorded_range="datetime", multivalued="false"
    )
    with pytest.raises(AssertionError, match="not categorical"):
        assert_incarnation_profile(package, schemas)


def test_multivalued_search_field_is_rejected(tmp_path: Path) -> None:
    package, schemas = _write_schema(
        tmp_path, search="channel", recorded_range="string", multivalued="true"
    )
    with pytest.raises(AssertionError, match="multivalued"):
        assert_incarnation_profile(package, schemas)


def test_external_schema_is_accepted(tmp_path: Path) -> None:
    package, schemas = _write_schema(
        tmp_path, search="channel", recorded_range="string", multivalued="false"
    )
    assert "channel" in assert_incarnation_profile(package, schemas)
    assert_incarnation_ships_no_schema(package)


def test_schema_inside_the_package_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    (package / "schema").mkdir(parents=True)
    (package / "schema" / "record.yaml").write_text(
        _RECORD.format(recorded_range="string", multivalued="false"), encoding="utf-8"
    )
    (package / "schema" / "profile.yaml").write_text(
        _PROFILE.format(search="channel", schema_path="schema/record.yaml"),
        encoding="utf-8",
    )
    with pytest.raises(AssertionError, match="inside the incarnation package"):
        assert_incarnation_profile(package, package)
    with pytest.raises(AssertionError, match="schema/record.yaml"):
        assert_incarnation_ships_no_schema(package)


def test_assert_incarnation_rejects_schema_mismatch(tmp_path: Path) -> None:
    package = tmp_path / "toy"
    shutil.copytree(TOY, package, ignore=shutil.ignore_patterns("__pycache__", ".*"))
    schemas = tmp_path / "schemas"
    (schemas / "sample").mkdir(parents=True)
    (schemas / "sample" / "record.yaml").write_text(
        _RECORD.format(recorded_range="string", multivalued="false"), encoding="utf-8"
    )
    (package / "schema" / "profile.yaml").write_text(
        _PROFILE.format(search="channel", schema_path="sample/record.yaml"),
        encoding="utf-8",
    )
    with httpx.Client() as client, pytest.raises(AssertionError) as caught:
        assert_incarnation(
            package,
            base_url="http://unused",
            token="unused",
            http_client=client,
            resource_id="unused",
            search_field="channel",
            search_value="unused",
            positive_fixture=package / "missing.npy",
            rejected_input=package / "missing.txt",
            schema_root=schemas,
        )
    assert str(caught.value) == (
        "resource type toy.record uses schema https://w3id.org/tegel/toy/record; "
        "the profile names https://w3id.org/tegel/sample/record"
    )


def _module(root: Path, source: str) -> None:
    path = root / "src" / "tegel" / "sample" / "dataserver" / "__init__.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")


def _write_project(root: Path, dependencies: str) -> None:
    text = _PROJECT.format(dependencies=dependencies)
    (root / "pyproject.toml").write_text(text, encoding="utf-8")


def _write_schema(
    root: Path, *, search: str, recorded_range: str, multivalued: str
) -> tuple[Path, Path]:
    schemas = root / "schemas"
    (schemas / "sample").mkdir(parents=True)
    (schemas / "sample" / "record.yaml").write_text(
        _RECORD.format(recorded_range=recorded_range, multivalued=multivalued),
        encoding="utf-8",
    )
    package = root / "pkg"
    (package / "schema").mkdir(parents=True)
    (package / "schema" / "profile.yaml").write_text(
        _PROFILE.format(search=search, schema_path="sample/record.yaml"),
        encoding="utf-8",
    )
    return package, schemas
