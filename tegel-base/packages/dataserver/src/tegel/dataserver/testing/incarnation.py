"""Conformance check for one dataserver incarnation package."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any

import httpx
import yaml

from tegel.core.contracts.resources import (
    DataDescriptor,
    StructureFamily,
    resource_from_catalog,
    resource_id_for,
    resource_uri,
)
from tegel.core.contracts.types import ResourceType, SearchProfile, class_slot_specs
from tegel.core.plugins import PluginContractError, catalog_metadata, describe_parts
from tegel.core.spec import schema_root_from_env
from tegel.dataserver.spec import load_spec, resolve_schema_path
from tegel.dataserver.testing.conformance import assert_read_only_api
from tegel.dataserver.testing.layout import assert_incarnation_layout

_ENTRY_GROUPS = (
    "tegel.resource_types",
    "tegel.extractors",
    "tegel.readers",
)
_RUNTIME_REQUIRED = "tegel-core"
_DEV_ALLOWED = frozenset({"tegel-dataserver", "pytest", "httpx"})
_FORBIDDEN_RANGES = frozenset(
    {"integer", "float", "double", "decimal", "datetime", "date", "time"}
)


def assert_incarnation_project(package_root: Path | str) -> None:
    """Fail when dependencies, scripts, or entry points leave the contract."""
    path = Path(package_root) / "pyproject.toml"
    if not path.is_file():
        raise AssertionError("pyproject.toml is missing")
    with path.open("rb") as handle:
        document = tomllib.load(handle)
    project = document.get("project")
    if not isinstance(project, dict):
        raise AssertionError("pyproject.toml has no project table")
    _check_runtime(project.get("dependencies"))
    _check_dev(document.get("dependency-groups"))
    if "scripts" in project:
        raise AssertionError("project.scripts is not part of a dataserver incarnation")
    _check_entry_points(project.get("entry-points"))


def assert_incarnation_ships_no_schema(package_root: Path | str) -> None:
    """Fail when the package contains a LinkML document (top-level ``classes:``)."""
    root = Path(package_root)
    found: list[str] = []
    for path in sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")]):
        if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
            continue
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(document, dict) and "classes" in document:
            found.append(path.relative_to(root).as_posix())
    if found:
        names = ", ".join(found)
        raise AssertionError(f"an incarnation names a schema and contains none: {names}")


def assert_incarnation_profile(
    package_root: Path | str,
    schema_root: Path | str | None = None,
) -> frozenset[str]:
    """Fail when the profile is unloadable, its schema is in the package, or a
    search field is not categorical. Return the class's slot names.

    ``schema_root`` defaults to ``TEGEL_SCHEMA_ROOT``.
    """
    return _profile_and_slots(package_root, schema_root)[1]


def _profile_and_slots(
    package_root: Path | str,
    schema_root: Path | str | None,
) -> tuple[SearchProfile, frozenset[str]]:
    root = Path(package_root).resolve()
    profile_path = root / "schema" / "profile.yaml"
    try:
        schema_dir = Path(schema_root) if schema_root is not None else schema_root_from_env()
        profile, schema = load_spec(profile_path, schema_dir)
        schema_file = resolve_schema_path(schema_dir, profile.schema_path)
    except (OSError, ValueError) as exc:
        raise AssertionError(str(exc)) from exc
    if schema_file == root or root in schema_file.parents:
        raise AssertionError(f"schema {profile.schema_path} is inside the incarnation package")
    slots = class_slot_specs(schema, profile.class_name)
    for name in profile.search:
        spec = slots[name]
        if spec.get("multivalued") is True:
            raise AssertionError(f"search field {name} is multivalued")
        if _range_name(spec.get("range")) in _FORBIDDEN_RANGES:
            raise AssertionError(f"search field {name} is not categorical")
    return profile, frozenset(slots)


def assert_incarnation(
    package_root: Path | str,
    *,
    base_url: str,
    token: str,
    http_client: httpx.Client,
    resource_id: str,
    search_field: str,
    search_value: str,
    positive_fixture: Path | str,
    rejected_input: Path | str,
    schema_root: Path | str | None = None,
) -> None:
    """Check layout, project file, profile, extractor, reader, and the read API.

    ``schema_root`` defaults to ``TEGEL_SCHEMA_ROOT``.
    """
    root = Path(package_root)
    assert_incarnation_layout(root)
    assert_incarnation_ships_no_schema(root)
    assert_incarnation_project(root)
    profile, slots = _profile_and_slots(root, schema_root)
    resource_type, extractor, reader = _load_plugins(root)
    if not isinstance(resource_type, ResourceType):
        raise AssertionError("tegel.resource_types must load a ResourceType")
    if resource_type.metadata_schema != profile.schema_id:
        raise AssertionError(
            f"resource type {resource_type.name} uses schema {resource_type.metadata_schema}; "
            f"the profile names {profile.schema_id}"
        )
    positive = str(positive_fixture)
    rejected = str(rejected_input)
    if extractor.validate(positive) is not True:
        raise AssertionError("validate on the positive fixture must be true")
    if extractor.validate(rejected) is not False:
        raise AssertionError("validate on the rejected input must be false")
    try:
        package = extractor.process(positive, {})
        stored = catalog_metadata(
            extractor,
            package,
            source_path=positive,
            profile=profile,
            slots=slots,
            resource_types={resource_type.name: resource_type},
        )
        resource_id_for(package.record, profile)
    except (PluginContractError, ValueError) as exc:
        raise AssertionError(str(exc)) from exc
    resource = resource_from_catalog(
        resource_id,
        resource_uri(base_url, resource_id),
        stored,
        profile=profile,
    )
    if not isinstance(reader.describe(resource), DataDescriptor):
        raise AssertionError("describe must return a DataDescriptor")
    try:
        descriptor = describe_parts([reader], resource)
    except PluginContractError as exc:
        raise AssertionError(str(exc)) from exc
    for part in descriptor.parts:
        if part.family is StructureFamily.array and not part.shape:
            raise AssertionError(f"array part {part.name} must include a shape hint")
    assert_read_only_api(
        base_url,
        token=token,
        http_client=http_client,
        resource_id=resource_id,
        search_field=search_field,
        search_value=search_value,
    )


def _check_runtime(dependencies: object) -> None:
    names = set(_requirement_names(dependencies, "dependencies"))
    if _RUNTIME_REQUIRED not in names or len(names) > 2:
        raise AssertionError(
            "runtime dependencies must be tegel-core plus at most one format library"
        )


def _check_dev(groups: object) -> None:
    if groups is None:
        return
    if not isinstance(groups, dict):
        raise AssertionError("dependency-groups must be a table")
    dev = groups.get("dev")
    if dev is None:
        return
    names = set(_requirement_names(dev, "dependency-groups.dev"))
    extra = names - _DEV_ALLOWED
    if extra:
        listed = ", ".join(sorted(extra))
        raise AssertionError(f"dev group has dependencies outside the contract: {listed}")


def _check_entry_points(entry_points: object) -> None:
    if not isinstance(entry_points, dict):
        raise AssertionError("project.entry-points is missing")
    extra = [name for name in entry_points if name not in _ENTRY_GROUPS]
    if extra:
        listed = ", ".join(sorted(extra))
        raise AssertionError(f"entry point group is not part of a dataserver incarnation: {listed}")
    shared: set[str] = set()
    for group in _ENTRY_GROUPS:
        table = entry_points.get(group)
        if not isinstance(table, dict) or len(table) != 1:
            raise AssertionError(f"{group} must register one name")
        shared.add(next(iter(table)))
    if len(shared) != 1:
        raise AssertionError("the three entry points must share one name")


def _load_plugins(package_root: Path) -> tuple[Any, Any]:
    path = package_root / "pyproject.toml"
    with path.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    entry_points = project["entry-points"]
    type_spec = _single_target(entry_points["tegel.resource_types"])
    extractor_spec = _single_target(entry_points["tegel.extractors"])
    reader_spec = _single_target(entry_points["tegel.readers"])
    try:
        resource_type = _load(type_spec)
        extractor = _load(extractor_spec)()
        reader = _load(reader_spec)()
    except Exception as exc:
        raise AssertionError(f"entry point did not construct: {exc}") from exc
    return resource_type, extractor, reader


def _single_target(table: dict[str, object]) -> str:
    value = next(iter(table.values()))
    if not isinstance(value, str) or ":" not in value:
        raise AssertionError("entry point target must be module:attr")
    return value


def _load(spec: str) -> Any:
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _requirement_names(requirements: object, label: str) -> list[str]:
    if not isinstance(requirements, list):
        raise AssertionError(f"{label} must be a list")
    names: list[str] = []
    for item in requirements:
        if not isinstance(item, str) or not item.strip():
            raise AssertionError(f"{label} entries must be strings")
        names.append(_distribution_name(item))
    return names


def _distribution_name(requirement: str) -> str:
    name = requirement.split(";", 1)[0].strip()
    name = name.split("[", 1)[0].strip()
    for marker in ("===", "==", "!=", "<=", ">=", "~=", "<", ">"):
        if marker in name:
            name = name.split(marker, 1)[0]
            break
    return name.strip().lower().replace("_", "-").replace(".", "-")


def _range_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "string"
    name = value.strip()
    if name.lower().startswith("xsd:"):
        name = name.split(":", 1)[1]
    return name.lower()
