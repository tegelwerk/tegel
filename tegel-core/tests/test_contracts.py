"""Wire-contract behavior taken from the data server specification."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from tegel.core.contracts.capabilities import Capabilities
from tegel.core.contracts.resources import (
    build_search_crate,
    hit_from_metadata,
    metadata_matches,
    parse_search_crate,
    resource_from_catalog,
    resource_id_for,
    resource_uri,
    split_catalog_metadata,
    unknown_filters,
)
from tegel.core.contracts.types import (
    ResourceType,
    SearchProfile,
    class_slot_specs,
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


def test_slot_order_ancestors_mixins_own() -> None:
    schema = {
        "slots": {"own_slot": {}, "base_slot": {}},
        "classes": {
            "Base": {"slots": ["base_slot"], "attributes": {"base_attr": {}}},
            "MixA": {"attributes": {"a": {}}},
            "MixB": {"is_a": "MixParent", "attributes": {"b": {}}},
            "MixParent": {"attributes": {"b_parent": {}}},
            "Record": {
                "is_a": "Base",
                "mixins": ["MixA", "MixB"],
                "slots": ["own_slot"],
                "attributes": {"own_attr": {}},
            },
        },
    }
    assert list(class_slot_specs(schema, "Record")) == [
        "base_slot",
        "base_attr",
        "a",
        "b_parent",
        "b",
        "own_slot",
        "own_attr",
    ]


def test_slot_usage_refines_mixin_slot() -> None:
    schema = {
        "slots": {"code": {"range": "string"}},
        "classes": {
            "Coded": {"slots": ["code"]},
            "Record": {"mixins": ["Coded"], "slot_usage": {"code": {"range": "CodeEnum"}}},
        },
    }
    assert class_slot_specs(schema, "Record")["code"]["range"] == "CodeEnum"


def test_linkml_does_not_follow_imports() -> None:
    schema = {"imports": ["linkml:types", "core"], "classes": {"A": {"attributes": {"x": {}}}}}
    with pytest.raises(ValueError, match="core"):
        linkml_attribute_names(schema, "A")


def test_profile_slots_must_be_attributes() -> None:
    names = set(linkml_attribute_names(SCHEMA, "Sample"))
    assert profile_unknown_slots(PROFILE, names) == []
    broken = PROFILE.model_copy(update={"search": ["sample_name", "missing"]})
    assert profile_unknown_slots(broken, names) == ["missing"]


def test_id_from_options_key() -> None:
    assert resource_id_for({"uuid": "abc"}, PROFILE, {"key": "chosen"}) == "chosen"


def test_id_from_identifier_field() -> None:
    assert resource_id_for({"uuid": "abc", "key": "ignored"}, PROFILE) == "abc"


def test_id_with_slash_rejected() -> None:
    with pytest.raises(ValueError, match="path segment"):
        resource_id_for({"uuid": "a/b"}, PROFILE)
    with pytest.raises(ValueError, match="path segment"):
        resource_id_for({"uuid": "abc"}, PROFILE, {"key": "a\\b"})


def test_id_missing_rejected() -> None:
    with pytest.raises(ValueError, match=r"options\['key'\] or record\['uuid'\]"):
        resource_id_for({"key": "k", "sample_name": "file.json"}, PROFILE)
    with pytest.raises(ValueError, match="record"):
        resource_id_for({"uuid": " padded "}, PROFILE)


def test_boolean_matches_true_false() -> None:
    assert metadata_matches({"flag": True}, {"flag": "true"})
    assert not metadata_matches({"flag": True}, {"flag": "True"})
    assert not metadata_matches({"flag": True}, {"flag": "1"})
    assert not metadata_matches({"flag": True}, {"flag": "false"})
    assert metadata_matches({"flag": False}, {"flag": "false"})
    assert metadata_matches({"kind": "even"}, {"kind": "even"})
    assert not metadata_matches({"kind": "even"}, {"kind": "odd"})
    assert not metadata_matches({}, {"kind": "even"})


def test_number_never_matches() -> None:
    assert not metadata_matches({"resolution": 1.45}, {"resolution": "1.45"})
    assert not metadata_matches({"count": 2}, {"count": "2"})


ENVELOPE = {
    "plugin_id": "example-v1",
    "source_name": "abc.json",
    "resource_type": "sample",
    "resource_type_version": "1",
    "payload_uri": "file:///data/abc.json",
    "relations": [{"rel": "parent", "target": "raw-1"}],
    "schema_id": "https://example.com/sample",
    "schema_version": "1",
    "class": "Sample",
}
RECORD = {"uuid": "abc", "sample_name": "north", "kind": "even", "recorded": "2026-10-01"}


def test_split_rejects_flat_row() -> None:
    flat = {**RECORD, "plugin_id": "example-v1", "source_name": "abc.json"}
    with pytest.raises(ValueError, match="not a Tegel 0.3 row"):
        split_catalog_metadata(flat)
    with pytest.raises(ValueError, match="not a Tegel 0.3 row"):
        split_catalog_metadata({"record": RECORD, "tegel": {"plugin_id": "x"}})
    with pytest.raises(ValueError, match="not a Tegel 0.3 row"):
        split_catalog_metadata({"record": RECORD, "tegel": ENVELOPE, "extra": 1})
    record, envelope = split_catalog_metadata({"record": RECORD, "tegel": ENVELOPE})
    assert record == RECORD
    assert envelope.class_name == "Sample"


def test_resource_from_catalog_projects_envelope() -> None:
    assert unknown_filters({"kind": "even", "nope": "x"}, PROFILE) == ["nope"]
    uri = resource_uri("https://data.example", "abc")
    resource = resource_from_catalog(
        "abc", uri, {"record": RECORD, "tegel": ENVELOPE}, profile=PROFILE
    )
    assert resource.metadata == RECORD
    assert resource.type == "sample"
    assert resource.type_version == "1"
    assert resource.provenance is not None
    assert resource.provenance.plugin_id == "example-v1"
    assert resource.provenance.source_name == "abc.json"
    assert resource.relations[0].target == "raw-1"
    assert resource.payload_uri == "file:///data/abc.json"
    assert resource.name == "north"
    assert resource.identifier == "abc"
    assert uri == "https://data.example/resources/abc"


def test_capabilities_schema_fields_optional() -> None:
    bare = Capabilities.model_validate({"server_version": "1"})
    assert bare.schema_id is None and bare.class_name is None
    full = Capabilities.model_validate(
        {
            "server_version": "1",
            "schema_id": "https://example.com/sample",
            "schema_version": "1",
            "class": "Sample",
        }
    )
    assert full.class_name == "Sample"
    assert full.model_dump(by_alias=True)["class"] == "Sample"


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
            record={},
            resource_type="sample",
            metrics={"sum": 1},
        )


def test_namespace_has_no_parent_init() -> None:
    import tegel.core

    parent = Path(tegel.core.__file__).resolve().parents[1]
    assert parent.name == "tegel"
    assert not (parent / "__init__.py").exists()
