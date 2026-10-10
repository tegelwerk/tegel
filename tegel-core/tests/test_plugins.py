"""Extractor contract and first-true selection."""

from pathlib import Path

import pytest

from tegel.core.contracts.types import ResourceType
from tegel.core.plugins import (
    GROUPS,
    Extractor,
    PluginContractError,
    ProcessedDataPackage,
    Registry,
    check_package,
    first_true,
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
            metadata={
                "plugin_id": self.plugin_id,
                "source_name": Path(input_path).name,
                "uuid": "record-1",
            },
            metrics={"characters": float(len(input_path))},
        )


class JsonPlugin(_JsonPlugin):
    def __init__(self) -> None:
        super().__init__()


class OtherPlugin(_JsonPlugin):
    def __init__(self) -> None:
        super().__init__("other-v1")


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
    source = tmp_path / "record.json"
    source.write_text("{}", encoding="utf-8")
    registry = Registry(
        {
            "tegel.extractors": {
                "b-plugin": OtherPlugin,
                "a-plugin": JsonPlugin,
            },
            "tegel.resource_types": {
                "sample": ResourceType(
                    name="sample",
                    version="1",
                    metadata_schema="https://example.com/sample",
                )
            },
        },
        versions={("tegel.extractors", "a-plugin"): "1.2.0"},
    )
    chosen = registry.extractor_for(str(source))
    assert chosen.plugin_id == "json-v1"
    package = check_package(chosen, chosen.process(str(source), {}))
    assert package.metadata["source_name"] == "record.json"
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
    missing = tmp_path / "missing.json"
    package = ProcessedDataPackage(
        payload_path=str(missing),
        metadata={"plugin_id": "json-v1", "source_name": "missing.json"},
        metrics={},
    )
    with pytest.raises(PluginContractError, match="readable"):
        check_package(plugin, package)
