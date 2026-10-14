"""Conformance check for one dataserver incarnation package."""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any

import httpx

from tegel.core.contracts.resources import (
    DataDescriptor,
    Resource,
    StructureFamily,
    resource_uri,
)
from tegel.core.plugins import PluginContractError, check_package
from tegel.dataserver.spec import load_spec
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


def assert_incarnation_profile(package_root: Path | str) -> None:
    """Fail when the profile is unloadable or a search field is not categorical."""
    profile_path = Path(package_root) / "schema" / "profile.yaml"
    try:
        profile, schema = load_spec(profile_path)
    except (OSError, ValueError) as exc:
        raise AssertionError(str(exc)) from exc
    attributes = _tree_root_attributes(schema)
    for name in profile.search:
        spec = attributes.get(name)
        if not isinstance(spec, dict):
            raise AssertionError(f"search field {name} has no attribute spec")
        if spec.get("multivalued") is True:
            raise AssertionError(f"search field {name} is multivalued")
        if _range_name(spec.get("range")) in _FORBIDDEN_RANGES:
            raise AssertionError(f"search field {name} is not categorical")


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
) -> None:
    """Check layout, project file, profile, extractor, reader, and the read API."""
    root = Path(package_root)
    assert_incarnation_layout(root)
    assert_incarnation_project(root)
    assert_incarnation_profile(root)
    extractor, reader = _load_plugins(root)
    positive = str(positive_fixture)
    rejected = str(rejected_input)
    if extractor.validate(positive) is not True:
        raise AssertionError("validate on the positive fixture must be true")
    if extractor.validate(rejected) is not False:
        raise AssertionError("validate on the rejected input must be false")
    try:
        package = check_package(extractor, extractor.process(positive, {}))
    except PluginContractError as exc:
        raise AssertionError(str(exc)) from exc
    type_name = package.metadata.get("resource_type")
    type_version = package.metadata.get("resource_type_version")
    resource = Resource(
        id=resource_id,
        uri=resource_uri(base_url, resource_id),
        type=type_name if isinstance(type_name, str) else None,
        type_version=type_version if isinstance(type_version, str) else None,
        metadata=package.metadata,
    )
    descriptor = reader.describe(resource)
    if not isinstance(descriptor, DataDescriptor):
        raise AssertionError("describe must return a DataDescriptor")
    if descriptor.family is StructureFamily.array and not descriptor.shape:
        raise AssertionError("an array descriptor must include shape")
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
    extractor_spec = _single_target(entry_points["tegel.extractors"])
    reader_spec = _single_target(entry_points["tegel.readers"])
    try:
        extractor = _load(extractor_spec)()
        reader = _load(reader_spec)()
    except Exception as exc:
        raise AssertionError(f"entry point did not construct: {exc}") from exc
    return extractor, reader


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


def _tree_root_attributes(schema: dict[str, Any]) -> dict[str, Any]:
    classes = schema.get("classes") or {}
    if not isinstance(classes, dict) or not classes:
        raise AssertionError("LinkML schema has no classes")
    chosen: dict[str, Any] | None = None
    for spec in classes.values():
        if isinstance(spec, dict) and spec.get("tree_root"):
            chosen = spec
            break
    if chosen is None:
        if len(classes) != 1:
            raise AssertionError("LinkML schema must mark one class tree_root")
        only = next(iter(classes.values()))
        if not isinstance(only, dict):
            raise AssertionError("LinkML class has no attributes")
        chosen = only
    attributes = chosen.get("attributes")
    if not isinstance(attributes, dict):
        raise AssertionError("LinkML class has no attributes")
    return attributes


def _range_name(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return "string"
    name = value.strip()
    if name.lower().startswith("xsd:"):
        name = name.split(":", 1)[1]
    return name.lower()
