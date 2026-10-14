"""Wire-contract behavior taken from the data server specification."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from tegel.core.contracts.capabilities import Capabilities
from tegel.core.contracts.resources import (
    CatalogEnvelope,
    DataDescriptor,
    DataPart,
    Resource,
    StructureFamily,
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
    "parts": [],
}
RECORD = {"uuid": "abc", "sample_name": "north", "kind": "even", "recorded": "2026-10-01"}


def test_split_rejects_flat_row() -> None:
    flat = {**RECORD, "plugin_id": "example-v1", "source_name": "abc.json"}
    with pytest.raises(ValueError, match="not a Tegel 0.4 row"):
        split_catalog_metadata(flat)
    with pytest.raises(ValueError, match="not a Tegel 0.4 row"):
        split_catalog_metadata({"record": RECORD, "tegel": {"plugin_id": "x"}})
    with pytest.raises(ValueError, match="not a Tegel 0.4 row"):
        split_catalog_metadata({"record": RECORD, "tegel": ENVELOPE, "extra": 1})
    without_parts = {key: value for key, value in ENVELOPE.items() if key != "parts"}
    with pytest.raises(ValueError, match="not a Tegel 0.4 row"):
        split_catalog_metadata({"record": RECORD, "tegel": without_parts})
    record, envelope = split_catalog_metadata({"record": RECORD, "tegel": ENVELOPE})
    assert record == RECORD
    assert envelope.class_name == "Sample"


def test_envelope_rejects_empty_schema_fields() -> None:
    with pytest.raises(ValidationError):
        CatalogEnvelope.model_validate({**ENVELOPE, "schema_id": ""})
    with pytest.raises(ValidationError):
        CatalogEnvelope.model_validate({**ENVELOPE, "schema_version": ""})
    assert CatalogEnvelope.model_validate(ENVELOPE).schema_id == ENVELOPE["schema_id"]


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


ZARR_PART = {
    "name": "frames",
    "family": "array",
    "media_type": "application/x-zarr",
    "asset_uri": "file:///data/frames.zarr",
    "designated": True,
    "shape": [4, 3],
}
TEXT_PART = {
    "name": "notes",
    "family": "bytes",
    "media_type": "text/plain",
    "asset_uri": "file:///data/notes.txt",
}


@pytest.mark.parametrize("name", ["frames", "a", "A1.b-c_d", "x" * 128])
def test_part_name_accepted(name: str) -> None:
    assert DataPart.model_validate({**TEXT_PART, "name": name}).name == name


@pytest.mark.parametrize("name", ["", ".hidden", "-x", "a/b", "a b", "x" * 129])
def test_part_name_rejected(name: str) -> None:
    with pytest.raises(ValidationError, match="part name"):
        DataPart.model_validate({**TEXT_PART, "name": name})


def test_part_names_unique() -> None:
    with pytest.raises(ValidationError, match="unique: notes"):
        DataDescriptor.model_validate({"parts": [TEXT_PART, TEXT_PART]})


def test_one_designated_part() -> None:
    other = {**ZARR_PART, "name": "other"}
    with pytest.raises(ValidationError, match="at most one part may be designated"):
        DataDescriptor.model_validate({"parts": [ZARR_PART, other]})


def test_non_file_asset_rejected() -> None:
    with pytest.raises(ValidationError, match="file: URI or an http"):
        DataPart.model_validate({**TEXT_PART, "asset_uri": "s3://bucket/notes.txt"})


def test_remote_part_requires_remote_mode() -> None:
    url = "https://beamline.example/api/v1/metadata/scan/42/frames"
    with pytest.raises(ValidationError, match="needs remote"):
        DataPart.model_validate({**ZARR_PART, "asset_uri": url})
    with pytest.raises(ValidationError, match="remote is only for http"):
        DataPart.model_validate({**ZARR_PART, "remote": "proxy"})
    assert DataPart.model_validate({**ZARR_PART, "asset_uri": url, "remote": "link"}).remote == (
        "link"
    )


def test_descriptor_round_trips_through_json() -> None:
    descriptor = DataDescriptor.model_validate(
        {"parts": [ZARR_PART, TEXT_PART], "payload": {**TEXT_PART, "name": "payload"}}
    )
    again = DataDescriptor.model_validate_json(descriptor.model_dump_json())
    assert again == descriptor
    assert again.parts[0].family is StructureFamily.array


def test_old_descriptor_shape_rejected() -> None:
    with pytest.raises(ValidationError):
        DataDescriptor.model_validate({"family": "bytes", "media_type": "text/plain"})


def test_envelope_with_parts_round_trips() -> None:
    parts = [{**ZARR_PART, "served_by": "tiled"}, {**TEXT_PART, "served_by": "tegel"}]
    payload = {**TEXT_PART, "name": "payload", "served_by": "tegel"}
    stored = {"record": RECORD, "tegel": {**ENVELOPE, "parts": parts, "payload": payload}}
    _, envelope = split_catalog_metadata(stored)
    assert [part.served_by for part in envelope.parts] == ["tiled", "tegel"]
    assert envelope.payload is not None and envelope.payload.name == "payload"
    dumped = envelope.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert split_catalog_metadata({"record": RECORD, "tegel": dumped})[1] == envelope
    resource = resource_from_catalog("abc", "https://d/resources/abc", stored)
    assert [part.name for part in resource.parts] == ["frames", "notes"]


def test_resource_parts_default_empty() -> None:
    resource = Resource.model_validate({"id": "abc", "uri": "https://d/resources/abc"})
    assert resource.parts == []
