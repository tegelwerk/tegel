"""The toy extractor claims an npy file and keeps the sidecar metadata."""

import json
import math
import struct
from pathlib import Path

from tegel.core.contracts.resources import Resource, StructureFamily, resource_uri
from tegel.core.plugins import check_package
from tegel.toy.dataserver import ToyExtractor, ToyReader


def test_extractor_reads_the_sidecar_and_reader_stops_at_the_header(tmp_path: Path) -> None:
    payload = tmp_path / "scan.npy"
    _write_npy(payload, (4, 5))
    sidecar = {
        "sample_name": "red-record",
        "uuid": "rec-derived",
        "recorded": "2026-01-02",
        "channel": "red",
        "relations": [{"rel": "derived-from", "target": "rec-raw"}],
    }
    payload.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")
    plugin = ToyExtractor()
    assert plugin.validate(str(payload)) is True
    assert plugin.validate(str(tmp_path / "notes.txt")) is False
    package = check_package(plugin, plugin.process(str(payload), {"key": "rec-derived"}))
    assert package.metadata["plugin_id"] == "toy-record"
    assert package.metadata["channel"] == "red"
    assert package.metadata["key"] == "rec-derived"
    resource = Resource(
        id="rec-derived",
        uri=resource_uri("http://example.test", "rec-derived"),
        type="toy.record",
        metadata=package.metadata,
    )
    descriptor = ToyReader().describe(resource)
    assert descriptor.family is StructureFamily.array
    assert descriptor.shape == [4, 5]
    assert descriptor.chunks == [[4, 5]]
    assert descriptor.data_type == "<f8"
    assert payload.stat().st_size > 80


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
