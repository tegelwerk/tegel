"""The toy extractor claims ``record.json``; the reader lists parts from headers only."""

from pathlib import Path

from tegel.core.contracts.resources import StructureFamily, resource_from_catalog, resource_uri
from tegel.core.plugins import catalog_metadata
from tegel.core.spec import load_spec, record_slots
from tegel.toy.dataserver import RECORD, ToyExtractor, ToyReader
from tegel.toy.dataserver.fixtures import write_record

EXAMPLES = Path(__file__).resolve().parents[2]
PROFILE, SCHEMA = load_spec(EXAMPLES / "toy" / "schema" / "profile.yaml", EXAMPLES / "schemas")
SLOTS = record_slots(PROFILE, SCHEMA)
RECORD_FIELDS = {
    "sample_name": "red-record",
    "uuid": "rec-derived",
    "recorded": "2026-01-02",
    "channel": "red",
    "calibrated": False,
}


def _resource(marker: Path):
    plugin = ToyExtractor()
    package = plugin.process(str(marker), {})
    stored = catalog_metadata(
        plugin,
        package,
        source_path=marker,
        profile=PROFILE,
        slots=SLOTS,
        resource_types={RECORD.name: RECORD},
    )
    uri = resource_uri("http://example.test", package.record["uuid"])
    return package, stored, resource_from_catalog(
        package.record["uuid"], uri, stored, profile=PROFILE
    )


def test_extractor_reads_record_json(tmp_path: Path) -> None:
    relations = [{"rel": "derived-from", "target": "rec-raw"}]
    marker = write_record(tmp_path / "rec", {**RECORD_FIELDS, "relations": relations})
    plugin = ToyExtractor()
    assert plugin.validate(str(marker)) is True
    assert plugin.validate(str(tmp_path / "rec" / "notes.txt")) is False
    package, stored, _ = _resource(marker)
    assert "relations" not in package.record
    assert package.record["channel"] == "red"
    assert package.relations[0].target == "rec-raw"
    assert stored["tegel"]["plugin_id"] == "toy-record"
    assert stored["tegel"]["source_name"] == "record.json"


def test_reader_lists_three_parts_and_the_payload(tmp_path: Path) -> None:
    marker = write_record(tmp_path / "rec", RECORD_FIELDS)
    _, _, resource = _resource(marker)
    descriptor = ToyReader().describe(resource)
    assert [part.name for part in descriptor.parts] == ["frames", "stats", "notes"]
    frames, stats, notes = descriptor.parts
    assert frames.family is StructureFamily.array and frames.designated
    assert frames.shape == [4, 3]
    assert frames.chunks is None
    assert stats.family is StructureFamily.table
    assert stats.media_type == "text/csv;header=present"
    assert notes.media_type == "text/plain"
    assert descriptor.payload is not None
    assert descriptor.payload.media_type == "application/json"
    assert descriptor.payload.asset_uri == marker.resolve().as_uri()


def test_reader_reads_only_zarr_json(tmp_path: Path) -> None:
    marker = write_record(tmp_path / "rec", RECORD_FIELDS, stats=False, notes=False)
    chunks = [
        path
        for path in (tmp_path / "rec" / "frames.zarr").rglob("*")
        if path.is_file() and path.name != "zarr.json"
    ]
    assert chunks
    for chunk in chunks:
        chunk.write_bytes(b"not a chunk")
    _, _, resource = _resource(marker)
    [frames] = ToyReader().describe(resource).parts
    assert frames.shape == [4, 3]


def test_uuid_defaults_to_the_directory_name(tmp_path: Path) -> None:
    marker = write_record(tmp_path / "plain", {}, frames=False, stats=False, notes=False)
    package = ToyExtractor().process(str(marker), {})
    assert package.record == {"uuid": "plain"}
