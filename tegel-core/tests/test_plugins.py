"""Extractor contract and first-true selection."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from tegel.core.contracts.types import ResourceType, SearchProfile
from tegel.core.plugins import (
    GROUPS,
    Extractor,
    PluginContractError,
    ProcessedDataPackage,
    Registry,
    catalog_metadata,
    check_package,
    first_true,
)

SLOTS = frozenset({"uuid", "title"})
SAMPLE = ResourceType(name="sample", version="3", metadata_schema="https://example.com/sample")
TYPES = {"sample": SAMPLE}
PROFILE = SearchProfile.model_validate(
    {
        "id": "https://example.com/profile",
        "name": "Sample search",
        "schema_id": "https://example.com/sample",
        "schema_version": "1.10",
        "schema_path": "sample.yaml",
        "class": "Sample",
        "search": ["title"],
        "properties": ["title"],
        "entity": {"name": "title", "identifier": "uuid", "dateCreated": "title"},
    }
)


class _JsonPlugin(Extractor):
    def __init__(self, plugin_id: str = "json-v1", calls: list[str] | None = None) -> None:
        self._plugin_id = plugin_id
        self.calls = calls if calls is not None else []

    @property
    def plugin_id(self) -> str:
        return self._plugin_id

    def validate(self, input_path: str) -> bool:
        self.calls.append(self._plugin_id)
        return input_path.endswith(".json")

    def process(self, input_path: str, options: dict) -> ProcessedDataPackage:
        return ProcessedDataPackage(
            payload_path=input_path,
            record={"uuid": "record-1", "title": "first"},
            resource_type="sample",
            payload_uri=Path(input_path).resolve().as_uri(),
            relations=[{"rel": "derived-from", "target": "raw-1"}],
            metrics={"characters": float(len(input_path))},
        )


class JsonPlugin(_JsonPlugin):
    def __init__(self) -> None:
        super().__init__()


class OtherPlugin(_JsonPlugin):
    def __init__(self) -> None:
        super().__init__("other-v1")


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "record.json"
    source.write_text("{}", encoding="utf-8")
    return source


def test_groups_match_the_spec() -> None:
    assert GROUPS == (
        "tegel.resource_types",
        "tegel.extractors",
        "tegel.readers",
        "tegel.previewers",
        "tegel.storage",
        "tegel.auth",
        "tegel.index",
    )


def test_first_true_does_not_call_later_plugins() -> None:
    calls: list[str] = []
    first = _JsonPlugin("first-v1", calls)
    second = _JsonPlugin("second-v1", calls)
    chosen = first_true([first, second], "record.json")
    assert chosen is first
    assert calls == ["first-v1"]


def test_first_true_skips_a_false_plugin() -> None:
    calls: list[str] = []

    class TextOnly(_JsonPlugin):
        def validate(self, input_path: str) -> bool:
            self.calls.append(self.plugin_id)
            return input_path.endswith(".txt")

    first = TextOnly("text-v1", calls)
    second = _JsonPlugin("json-v1", calls)
    chosen = first_true([first, second], "record.json")
    assert chosen is second
    assert calls == ["text-v1", "json-v1"]


def test_no_match_raises() -> None:
    plugin = _JsonPlugin()
    with pytest.raises(PluginContractError, match="No plugin"):
        first_true([plugin], "record.txt")


def test_registry_checks_contract_and_selects_by_name_order(tmp_path: Path) -> None:
    source = _source(tmp_path)
    registry = Registry(
        {
            "tegel.extractors": {
                "b-plugin": OtherPlugin,
                "a-plugin": JsonPlugin,
            },
            "tegel.resource_types": {"sample": SAMPLE},
        },
        versions={("tegel.extractors", "a-plugin"): "1.2.0"},
    )
    chosen = registry.extractor_for(str(source))
    assert chosen.plugin_id == "json-v1"
    package = check_package(
        chosen,
        chosen.process(str(source), {}),
        slots=SLOTS,
        resource_types=registry.resource_types(),
    )
    assert package.record["uuid"] == "record-1"
    installed = [row for row in registry.installed() if row.group == "tegel.extractors"]
    assert installed[0].name == "a-plugin"
    assert installed[0].version == "1.2.0"
    assert registry.resource_types()["sample"].metadata_schema.endswith("/sample")


def test_duplicate_plugin_id_is_rejected() -> None:
    class SameId(JsonPlugin):
        pass

    with pytest.raises(PluginContractError, match="already used"):
        Registry({"tegel.extractors": {"a": JsonPlugin, "b": SameId}})


def test_non_bool_validate_is_rejected() -> None:
    class Bad(_JsonPlugin):
        def validate(self, input_path: str) -> bool:
            return "yes"  # type: ignore[return-value]

    registry = Registry({"tegel.extractors": {"bad": Bad}})
    with pytest.raises(PluginContractError, match="bool"):
        registry.extractor_for("record.json")


def test_package_requires_a_real_file(tmp_path: Path) -> None:
    plugin = JsonPlugin()
    package = ProcessedDataPackage(
        payload_path=str(tmp_path / "missing.json"),
        record={"uuid": "x"},
        resource_type="sample",
        metrics={},
    )
    with pytest.raises(PluginContractError, match="readable"):
        check_package(plugin, package, slots=SLOTS, resource_types=TYPES)


def test_record_key_outside_class_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    plugin = JsonPlugin()
    package = plugin.process(str(source), {})
    package.record["detector_distance"] = "120"
    with pytest.raises(PluginContractError, match="record keys are not class slots: detector"):
        check_package(plugin, package, slots=SLOTS, resource_types=TYPES)


def test_unknown_resource_type_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    plugin = JsonPlugin()
    package = plugin.process(str(source), {}).model_copy(update={"resource_type": "other"})
    with pytest.raises(PluginContractError, match="'other' is not registered"):
        check_package(plugin, package, slots=SLOTS, resource_types=TYPES)


def test_relations_validated() -> None:
    with pytest.raises(ValidationError, match="target"):
        ProcessedDataPackage(
            payload_path="record.json",
            record={},
            resource_type="sample",
            relations=[{"rel": "derived-from"}],
            metrics={},
        )


def test_catalog_metadata_has_two_blocks(tmp_path: Path) -> None:
    source = _source(tmp_path)
    plugin = JsonPlugin()
    stored = catalog_metadata(
        plugin,
        plugin.process(str(source), {}),
        source_path=source,
        profile=PROFILE,
        slots=SLOTS,
        resource_types=TYPES,
    )
    assert set(stored) == {"record", "tegel"}
    assert stored["record"] == {"uuid": "record-1", "title": "first"}
    assert stored["tegel"] == {
        "plugin_id": "json-v1",
        "source_name": "record.json",
        "resource_type": "sample",
        "resource_type_version": "3",
        "payload_uri": source.resolve().as_uri(),
        "relations": [{"rel": "derived-from", "target": "raw-1"}],
        "schema_id": "https://example.com/sample",
        "schema_version": "1.10",
        "class": "Sample",
    }


def test_catalog_metadata_runs_the_package_check(tmp_path: Path) -> None:
    plugin = JsonPlugin()
    package = plugin.process(str(tmp_path / "missing.json"), {})
    with pytest.raises(PluginContractError, match="readable"):
        catalog_metadata(
            plugin,
            package,
            source_path=tmp_path / "missing.json",
            profile=PROFILE,
            slots=SLOTS,
            resource_types=TYPES,
        )
