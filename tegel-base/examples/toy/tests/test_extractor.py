"""The toy extractor claims an npy file and keeps the sidecar fields as the record."""

import json
import math
import struct
from pathlib import Path

from tegel.core.contracts.resources import StructureFamily, resource_from_catalog, resource_uri
from tegel.core.plugins import catalog_metadata
from tegel.core.spec import load_spec, record_slots
from tegel.toy.dataserver import RECORD, ToyExtractor, ToyReader

EXAMPLES = Path(__file__).resolve().parents[2]
PROFILE, SCHEMA = load_spec(EXAMPLES / "toy" / "schema" / "profile.yaml", EXAMPLES / "schemas")
SLOTS = record_slots(PROFILE, SCHEMA)


def test_extractor_reads_the_sidecar_and_reader_stops_at_the_header(tmp_path: Path) -> None:
    payload = tmp_path / "scan.npy"
    _write_npy(payload, (4, 5))
    sidecar = {
        "sample_name": "red-record",
        "uuid": "rec-derived",
        "recorded": "2026-01-02",
        "channel": "red",
        "calibrated": False,
        "relations": [{"rel": "derived-from", "target": "rec-raw"}],
    }
    payload.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")
    plugin = ToyExtractor()
    assert plugin.validate(str(payload)) is True
    assert plugin.validate(str(tmp_path / "notes.txt")) is False
    package = plugin.process(str(payload), {})
    assert "relations" not in package.record
    assert package.record["channel"] == "red"
    assert package.relations[0].target == "rec-raw"
    stored = catalog_metadata(
        plugin,
        package,
        source_path=payload,
        profile=PROFILE,
        slots=SLOTS,
        resource_types={RECORD.name: RECORD},
    )
    assert stored["tegel"]["plugin_id"] == "toy-record"
    assert stored["tegel"]["source_name"] == "scan.npy"
    resource = resource_from_catalog(
        "rec-derived",
        resource_uri("http://example.test", "rec-derived"),
        stored,
        profile=PROFILE,
    )
    descriptor = ToyReader().describe(resource)
    assert descriptor.family is StructureFamily.array
    assert descriptor.shape == [4, 5]
    assert descriptor.chunks == [[4, 5]]
    assert descriptor.data_type == "<f8"
    assert payload.stat().st_size > 80


def test_uuid_defaults_to_the_file_stem(tmp_path: Path) -> None:
    payload = tmp_path / "plain.npy"
    _write_npy(payload, (1,))
    package = ToyExtractor().process(str(payload), {})
    assert package.record == {"uuid": "plain"}


def _write_npy(path: Path, shape: tuple[int, ...]) -> None:
    raw = str({"descr": "<f8", "fortran_order": False, "shape": shape}).encode("ascii")
    pad = (64 - ((10 + len(raw) + 1) % 64)) % 64
    header = raw + b" " * pad + b"\n"
    payload = b"\x00" * (8 * math.prod(shape))
    with path.open("wb") as handle:
        handle.write(b"\x93NUMPY\x01\x00")
        handle.write(struct.pack("<H", len(header)))
        handle.write(header)
        handle.write(payload)
