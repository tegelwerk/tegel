"""Wire-contract behavior taken from the data server specification."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from tegel.core.contracts.resources import (
    build_search_crate,
    hit_from_metadata,
    metadata_matches,
    parse_search_crate,
    resource_from_catalog,
    resource_id_from_metadata,
    resource_uri,
    unknown_filters,
)
from tegel.core.contracts.types import (
    ResourceType,
    SearchProfile,
    linkml_attribute_names,
    profile_unknown_slots,
    type_chain,
)
from tegel.core.plugins import ProcessedDataPackage

SCHEMA = {
    "classes": {
        "Sample": {
            "attributes": {
                "uuid": {},
                "sample_name": {},
                "kind": {},
                "recorded": {},
            },
        }
    }
}

PROFILE = SearchProfile.model_validate(
    {
        "id": "https://example.com/profile",
        "name": "Sample search",
        "schema_id": "https://example.com/sample",
        "schema_version": "1",
        "schema_path": "sample.yaml",
        "class": "Sample",
        "search": ["sample_name", "kind"],
        "properties": ["sample_name", "kind"],
        "entity": {
            "name": "sample_name",
            "identifier": "uuid",
            "dateCreated": "recorded",
        },
    }
)


def test_linkml_named_class_attributes() -> None:
    assert linkml_attribute_names(SCHEMA, "Sample") == ["uuid", "sample_name", "kind", "recorded"]


def test_linkml_slots_and_is_a_ancestors() -> None:
    schema = {
        "imports": ["linkml:types"],
        "slots": {"uuid": {"range": "string"}, "energy": {"range": "float"}},
        "classes": {
            "Base": {"slots": ["uuid"], "attributes": {"plugin_id": {}}},
            "Series": {"is_a": "Base", "slots": ["energy"]},
            "Pin": {"is_a": "Series", "attributes": {"pin_id": {}}},
            "Other": {"attributes": {"unrelated": {}}},
        },
    }
    assert linkml_attribute_names(schema, "Pin") == [
        "uuid",
        "plugin_id",
        "energy",
        "pin_id",
    ]


def test_linkml_rejects_a_missing_class_and_a_cycle() -> None:
    with pytest.raises(ValueError, match="Missing"):
        linkml_attribute_names(SCHEMA, "Missing")
    looped = {"classes": {"A": {"is_a": "B", "slots": ["x"]}, "B": {"is_a": "A"}}}
    with pytest.raises(ValueError, match="cycle"):
        linkml_attribute_names(looped, "A")


def test_linkml_does_not_follow_imports() -> None:
    schema = {"imports": ["linkml:types", "core"], "classes": {"A": {"attributes": {"x": {}}}}}
    with pytest.raises(ValueError, match="core"):
        linkml_attribute_names(schema, "A")


def test_profile_slots_must_be_attributes() -> None:
    names = set(linkml_attribute_names(SCHEMA, "Sample"))
    assert profile_unknown_slots(PROFILE, names) == []
    broken = PROFILE.model_copy(update={"search": ["sample_name", "missing"]})
    assert profile_unknown_slots(broken, names) == ["missing"]


def test_resource_id_uses_explicit_fields_only() -> None:
    assert resource_id_from_metadata({"uuid": "abc"}) == "abc"
    assert resource_id_from_metadata({"source_name": "file.json"}, {"key": "chosen"}) == "chosen"
    with pytest.raises(ValueError, match="explicit"):
        resource_id_from_metadata({"source_name": "file.json"})
    with pytest.raises(ValueError, match="path segment"):
        resource_id_from_metadata({"uuid": "a/b"})


def test_catalog_projection_and_search_match() -> None:
    metadata = {
        "uuid": "abc",
        "sample_name": "north",
        "kind": "even",
        "recorded": "2026-10-01",
        "plugin_id": "example-v1",
        "source_name": "abc.json",
        "relations": [{"rel": "parent", "target": "raw-1"}],
    }
    assert metadata_matches(metadata, {"kind": "even"})
    assert not metadata_matches(metadata, {"kind": "odd"})
    assert unknown_filters({"kind": "even", "nope": "x"}, PROFILE) == ["nope"]
    uri = resource_uri("https://data.example", "abc")
    resource = resource_from_catalog("abc", uri, metadata, profile=PROFILE)
    assert resource.provenance is not None
    assert resource.provenance.plugin_id == "example-v1"
    assert resource.relations[0].target == "raw-1"
    assert resource.name == "north"
    assert uri == "https://data.example/resources/abc"


def test_search_crate_round_trip() -> None:
    metadata = {
        "uuid": "abc",
        "sample_name": "north",
        "kind": "even",
        "recorded": "2026-10-01",
    }
    uri = resource_uri("https://data.example", "abc")
    hit = hit_from_metadata(uri, metadata, PROFILE, fallback_id="abc")
    document = build_search_crate([hit], PROFILE, "https://data.example")
    parsed = parse_search_crate(document)
    assert parsed.document == "ro-crate"
    assert parsed.count == 1
    assert parsed.hits[0].uri == uri
    assert parsed.hits[0].properties["kind"] == "even"
    assert document["@graph"][0]["conformsTo"][1]["@id"] == PROFILE.id


def test_type_chain_and_cycle() -> None:
    types = {
        "array.nd": ResourceType(name="array.nd", version="1", metadata_schema="array"),
        "image.series": ResourceType(
            name="image.series",
            version="1",
            metadata_schema="image",
            parent="array.nd",
        ),
    }
    assert [item.name for item in type_chain(types, "image.series")] == [
        "image.series",
        "array.nd",
    ]
    types["array.nd"] = ResourceType(
        name="array.nd",
        version="1",
        metadata_schema="array",
        parent="image.series",
    )
    with pytest.raises(ValueError, match="cycle"):
        type_chain(types, "image.series")


def test_metrics_reject_ints() -> None:
    with pytest.raises(ValidationError):
        ProcessedDataPackage(
            payload_path="record.json",
            metadata={"plugin_id": "example-v1", "source_name": "record.json"},
            metrics={"sum": 1},
        )


def test_namespace_has_no_parent_init() -> None:
    import tegel.core

    parent = Path(tegel.core.__file__).resolve().parents[1]
    assert parent.name == "tegel"
    assert not (parent / "__init__.py").exists()
