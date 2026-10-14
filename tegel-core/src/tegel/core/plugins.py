"""Entry-point discovery and the ETL plugin contract.

The data server lists these plugins. The ETL application runs extractors.
Selection is deterministic: entry-point name order, then the first
``validate`` that returns true.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from importlib.metadata import EntryPoints, entry_points
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tegel.core.contracts.capabilities import InstalledPlugin, Principal
from tegel.core.contracts.resources import (
    ENVELOPE_KEY,
    RECORD_KEY,
    CatalogEnvelope,
    DataDescriptor,
    DataPart,
    Relation,
    Resource,
    dump_envelope,
    split_catalog_metadata,
)
from tegel.core.contracts.types import ResourceType, SearchProfile

GROUPS: tuple[str, ...] = (
    "tegel.resource_types",
    "tegel.extractors",
    "tegel.readers",
    "tegel.previewers",
    "tegel.storage",
    "tegel.auth",
    "tegel.index",
)


class PluginContractError(ValueError):
    """A loaded plugin does not match its contract."""


class ProcessedDataPackage(BaseModel):
    """What an extractor returns for one input.

    ``record`` holds domain fields only. Tegel builds its own ``tegel`` block
    from the plugin, the input path, and the registered resource type.
    """

    model_config = ConfigDict(extra="forbid")

    payload_path: str = Field(min_length=1)
    record: dict[str, Any]
    resource_type: str = Field(min_length=1)
    payload_uri: str | None = Field(default=None, min_length=1)
    relations: list[Relation] = Field(default_factory=list)
    metrics: dict[str, float]

    @field_validator("metrics", mode="before")
    @classmethod
    def _metrics_are_floats(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        for key, item in value.items():
            if type(item) is not float:
                raise ValueError(f"metrics[{key!r}] must be a float")
        return value


class Extractor(ABC):
    """ETL plugin. Constructed with no arguments."""

    @property
    @abstractmethod
    def plugin_id(self) -> str:
        """Unique id, unique among installed extractors."""

    @abstractmethod
    def validate(self, input_path: str) -> bool:
        """Return whether this plugin can read the input."""

    @abstractmethod
    def process(self, input_path: str, options: dict[str, Any]) -> ProcessedDataPackage:
        """Turn one input path into a package."""


class Reader(Protocol):
    """Describe how to read a resource. ``describe`` does not load an array."""

    def validate(self, resource: Resource) -> bool:
        """Return whether this reader handles the resource."""

    def describe(self, resource: Resource) -> DataDescriptor:
        """Return the resource's data parts and payload.

        Staging calls this once. It reads headers and store metadata only,
        never array chunks or table rows.
        """


class Previewer(Protocol):
    """Return a descriptor for a preview of a resource."""

    def validate(self, resource: Resource) -> bool:
        """Return whether this previewer handles the resource."""

    def preview(self, resource: Resource) -> DataDescriptor:
        """Return a preview descriptor."""


class Storage(Protocol):
    """Open bytes already recorded by the catalog."""

    def open_bytes(self, uri: str) -> Any:
        """Return a binary reader for ``uri``."""

    def open_range(self, uri: str, start: int, end: int) -> bytes:
        """Return bytes ``start`` through ``end`` inclusive of ``uri``."""

    def size(self, uri: str) -> int:
        """Return the size of ``uri`` in bytes."""


class AuthProvider(Protocol):
    """Authenticate a bearer header. The reference provider is Keycloak."""

    name: str

    def authenticate(self, authorization: str | None) -> Principal:
        """Return the caller, or raise if the header is rejected."""


class Index(Protocol):
    """Read-only catalog access."""

    def get(self, resource_id: str) -> Resource | None:
        """Return one resource, or ``None`` when it is absent."""

    def keys(self) -> list[str]:
        """Return catalog ids."""


def check_plugin(
    plugin_cls: type,
    origin: str,
    claimed: dict[str, str],
) -> Extractor:
    """Reject an extractor class that does not match the contract."""
    if not isinstance(plugin_cls, type) or not issubclass(plugin_cls, Extractor):
        raise PluginContractError(f"{origin}: must be an Extractor subclass")
    try:
        plugin = plugin_cls()
    except PluginContractError:
        raise
    except Exception as exc:
        raise PluginContractError(
            f"{origin}: must construct with no arguments ({exc})"
        ) from exc
    return _check_instance(plugin, origin, claimed)


def _check_instance(
    plugin: Extractor,
    origin: str,
    claimed: dict[str, str],
) -> Extractor:
    try:
        plugin_id = plugin.plugin_id
    except Exception as exc:
        raise PluginContractError(
            f"{origin}: plugin_id must be a non-empty string ({exc})"
        ) from exc
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        raise PluginContractError(f"{origin}: plugin_id must be a non-empty string")
    previous = claimed.get(plugin_id)
    if previous is not None:
        raise PluginContractError(
            f"{origin}: plugin_id {plugin_id!r} is already used by {previous}"
        )
    _require_call(
        plugin.validate,
        ("input",),
        f"{origin}: validate must accept an input path",
    )
    _require_call(
        plugin.process,
        ("input", {}),
        f"{origin}: process must accept an input path and an options dict",
    )
    claimed[plugin_id] = origin
    return plugin


def check_decision(plugin: Extractor, decision: object) -> bool:
    """Reject a validate result that is not a bool."""
    if not isinstance(decision, bool):
        raise PluginContractError(f"{plugin.plugin_id}: validate must return a bool")
    return decision


def check_package(
    plugin: Extractor,
    package: object,
    *,
    slots: Collection[str],
    resource_types: Mapping[str, ResourceType],
) -> ProcessedDataPackage:
    """Reject a process result that is not a usable package.

    ``slots`` are the slot names of the profile's class; every ``record`` key
    must be one. ``resource_types`` maps registered type names to types.
    """
    plugin_id = plugin.plugin_id
    if not isinstance(package, ProcessedDataPackage):
        raise PluginContractError(f"{plugin_id}: process must return a ProcessedDataPackage")
    if not Path(package.payload_path).is_file():
        raise PluginContractError(f"{plugin_id}: payload_path must be a readable file")
    if not isinstance(package.record, dict):
        raise PluginContractError(f"{plugin_id}: record must be a dict")
    allowed = set(slots)
    extra = sorted(str(key) for key in package.record if key not in allowed)
    if extra:
        names = ", ".join(extra)
        raise PluginContractError(f"{plugin_id}: record keys are not class slots: {names}")
    if package.resource_type not in resource_types:
        raise PluginContractError(
            f"{plugin_id}: resource_type {package.resource_type!r} is not registered"
        )
    for key, value in package.metrics.items():
        if not isinstance(key, str) or not key:
            raise PluginContractError(f"{plugin_id}: metrics keys must be non-empty strings")
        if not isinstance(value, float):
            raise PluginContractError(f"{plugin_id}: metrics[{key!r}] must be a float")
    return package


def catalog_metadata(
    plugin: Extractor,
    package: object,
    *,
    source_path: str | Path,
    profile: SearchProfile,
    slots: Collection[str],
    resource_types: Mapping[str, ResourceType],
) -> dict[str, Any]:
    """Check a package and return the stored row ``{"record": ..., "tegel": ...}``."""
    checked = check_package(plugin, package, slots=slots, resource_types=resource_types)
    envelope = CatalogEnvelope(
        plugin_id=plugin.plugin_id,
        source_name=Path(source_path).name,
        resource_type=checked.resource_type,
        resource_type_version=resource_types[checked.resource_type].version,
        payload_uri=checked.payload_uri,
        relations=checked.relations,
        schema_id=profile.schema_id,
        schema_version=profile.schema_version,
        class_name=profile.class_name,
    )
    return {RECORD_KEY: dict(checked.record), ENVELOPE_KEY: dump_envelope(envelope)}


def with_parts(
    stored: dict[str, Any],
    parts: Sequence[DataPart],
    payload: DataPart | None,
) -> dict[str, Any]:
    """Return ``stored`` with the envelope's ``parts`` and ``payload`` replaced."""
    _, envelope = split_catalog_metadata(stored)
    updated = envelope.model_copy(update={"parts": list(parts), "payload": payload})
    checked = CatalogEnvelope.model_validate(dump_envelope(updated))
    return {RECORD_KEY: dict(stored[RECORD_KEY]), ENVELOPE_KEY: dump_envelope(checked)}


def schema_mismatch(
    resource_types: Mapping[str, ResourceType],
    profile: SearchProfile,
) -> str | None:
    """Return why the resource types do not serve the profile's schema, or ``None``."""
    if not resource_types:
        return "no tegel.resource_types entry point is installed"
    for name in sorted(resource_types):
        found = resource_types[name].metadata_schema
        if found != profile.schema_id:
            return (
                f"resource type {name} uses schema {found}; "
                f"the profile names {profile.schema_id}"
            )
    return None


def _require_call(method: object, arguments: tuple[object, ...], message: str) -> None:
    if not callable(method):
        raise PluginContractError(message)
    try:
        inspect.signature(method).bind(*arguments)
    except (TypeError, ValueError) as exc:
        raise PluginContractError(message) from exc


def first_true(plugins: Sequence[Any], argument: Any) -> Any:
    """Return the first plugin whose ``validate(argument)`` is true.

    Later plugins are not called. If none return true, raise
    ``PluginContractError``.
    """
    for plugin in plugins:
        decision = plugin.validate(argument)
        if check_decision(plugin, decision):
            return plugin
    raise PluginContractError("No plugin validated the input")


def readers_in_order(registry: Registry) -> list[Reader]:
    """Instantiate ``tegel.readers`` classes in entry-point name order."""
    loaded: list[Reader] = []
    group = registry.groups["tegel.readers"]
    for name in sorted(group):
        value = group[name]
        if not isinstance(value, type):
            raise PluginContractError(f"tegel.readers:{name}: must be a class")
        try:
            loaded.append(value())
        except Exception as exc:
            raise PluginContractError(
                f"tegel.readers:{name}: must construct with no arguments ({exc})"
            ) from exc
    return loaded


def describe_parts(readers: Sequence[Reader], resource: Resource) -> DataDescriptor:
    """Select the first reader that validates ``resource`` and check its descriptor.

    Any failure, including a reader that raises, is a ``PluginContractError``.
    """
    try:
        reader = first_true(readers, resource)
    except PluginContractError as exc:
        raise PluginContractError(f"no reader validated resource type {resource.type}") from exc
    name = type(reader).__name__
    try:
        described = reader.describe(resource)
    except Exception as exc:
        raise PluginContractError(f"{name}.describe failed: {exc}") from exc
    try:
        if isinstance(described, DataDescriptor):
            return DataDescriptor.model_validate(described.model_dump())
        return DataDescriptor.model_validate(described)
    except ValueError as exc:
        raise PluginContractError(f"{name}.describe returned an invalid descriptor: {exc}") from exc


class _EntryPoint:
    """Enough of ``importlib.metadata.EntryPoint`` for tests."""

    def __init__(self, name: str, loader: Callable[[], Any], version: str = "0") -> None:
        self.name = name
        self._loader = loader
        self.dist_version = version

    def load(self) -> Any:
        return self._loader()


def _version_of(entry_point: Any) -> str:
    dist = getattr(entry_point, "dist", None)
    version = getattr(dist, "version", None)
    if isinstance(version, str) and version:
        return version
    own = getattr(entry_point, "dist_version", None)
    if isinstance(own, str) and own:
        return own
    return "0"


def load_group(
    group: str,
    entry_point_list: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Load one entry-point group, sorted by name."""
    if group not in GROUPS:
        raise PluginContractError(f"Unknown entry-point group {group}")
    selected: Iterable[Any]
    if entry_point_list is None:
        found: EntryPoints = entry_points(group=group)
        selected = found
    else:
        selected = entry_point_list
    loaded: dict[str, Any] = {}
    for entry_point in sorted(selected, key=lambda item: item.name):
        try:
            value = entry_point.load()
        except PluginContractError:
            raise
        except Exception as exc:
            raise PluginContractError(
                f"{group}:{entry_point.name}: failed to load ({exc})"
            ) from exc
        loaded[entry_point.name] = value
    return loaded


class Registry:
    """Plugins discovered from entry points, or injected in tests."""

    def __init__(
        self,
        groups: dict[str, dict[str, Any]] | None = None,
        *,
        versions: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.groups: dict[str, dict[str, Any]] = {}
        self._versions = dict(versions or {})
        if groups is None:
            for name in GROUPS:
                found = entry_points(group=name)
                self.groups[name] = load_group(name, found)
                for entry_point in found:
                    self._versions[(name, entry_point.name)] = _version_of(entry_point)
        else:
            unknown = set(groups) - set(GROUPS)
            if unknown:
                raise PluginContractError(f"Unknown entry-point group {sorted(unknown)[0]}")
            self.groups = {name: dict(groups.get(name, {})) for name in GROUPS}
        self.extractors = self._load_extractors(self.groups["tegel.extractors"])

    def _load_extractors(self, raw: dict[str, Any]) -> dict[str, Extractor]:
        claimed: dict[str, str] = {}
        loaded: dict[str, Extractor] = {}
        for name in sorted(raw):
            origin = f"tegel.extractors:{name}"
            plugin = check_plugin(raw[name], origin, claimed)
            loaded[name] = plugin
        return loaded

    def resource_types(self) -> dict[str, ResourceType]:
        found: dict[str, ResourceType] = {}
        for name, value in self.groups["tegel.resource_types"].items():
            if not isinstance(value, ResourceType):
                raise PluginContractError(
                    f"tegel.resource_types:{name}: must be a ResourceType"
                )
            found[value.name] = value
        return found

    def extractor_for(self, input_path: str) -> Extractor:
        """First extractor, in entry-point name order, that accepts the path."""
        ordered = [self.extractors[name] for name in sorted(self.extractors)]
        return first_true(ordered, input_path)

    def installed(self) -> list[InstalledPlugin]:
        """Plugins to report from ``GET /capabilities``."""
        rows: list[InstalledPlugin] = []
        for group in GROUPS:
            for name in sorted(self.groups[group]):
                version = self._versions.get((group, name), "0")
                rows.append(InstalledPlugin(group=group, name=name, version=version))
        return rows
