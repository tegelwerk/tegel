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
            "tree_root": True,
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
        "schema": "sample.yaml",
        "search": ["sample_name", "kind"],
        "properties": ["sample_name", "kind"],
        "entity": {
            "name": "sample_name",
            "identifier": "uuid",
            "dateCreated": "recorded",
        },
    }
)


def test_linkml_tree_root_attributes() -> None:
    assert linkml_attribute_names(SCHEMA) == ["uuid", "sample_name", "kind", "recorded"]


def test_linkml_single_class_without_tree_root() -> None:
    schema = {"classes": {"Only": {"attributes": {"uuid": {}}}}}
    assert linkml_attribute_names(schema) == ["uuid"]


def test_linkml_requires_one_root() -> None:
    schema = {"classes": {"A": {"attributes": {"x": {}}}, "B": {"attributes": {"y": {}}}}}
    with pytest.raises(ValueError, match="tree_root"):
        linkml_attribute_names(schema)


def test_profile_slots_must_be_attributes() -> None:
    names = set(linkml_attribute_names(SCHEMA))
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
