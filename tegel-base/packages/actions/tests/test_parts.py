"""The ``parts`` staging stage: describe once, plan ``served_by``, register Tiled children."""

import json
import sqlite3
from pathlib import Path

import pytest

from tegel.actions.parts import readable_roots
from tegel.actions.staging import StagingError, write_staging
from tegel.core.contracts.resources import DataDescriptor, StructureFamily
from tegel.toy.dataserver import ToyReader
from tegel.toy.dataserver.fixtures import write_record

PROFILE = Path(__file__).resolve().parents[3] / "examples" / "toy" / "schema" / "profile.yaml"
RECORD = {"uuid": "rec-parts", "sample_name": "parts", "channel": "bin", "calibrated": True}


@pytest.fixture
def profile(monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))


def _children(staging: Path, key: str) -> dict[str, str]:
    with sqlite3.connect(staging) as connection:
        rows = connection.execute(
            """
            SELECT child.key, child.structure_family FROM nodes AS child
            JOIN nodes AS parent ON child.parent = parent.id
            WHERE parent.key = ? AND parent.parent = (SELECT id FROM nodes WHERE parent IS NULL)
            """,
            (key,),
        ).fetchall()
    return dict(rows)


def _structure(staging: Path, key: str, child: str) -> dict:
    with sqlite3.connect(staging) as connection:
        (structure,) = connection.execute(
            """
            SELECT structures.structure FROM structures
            JOIN data_sources ON data_sources.structure_id = structures.id
            JOIN nodes AS node ON data_sources.node_id = node.id
            JOIN nodes AS parent ON node.parent = parent.id
            WHERE parent.key = ? AND node.key = ?
            """,
            (key, child),
        ).fetchone()
    return json.loads(structure)


def _row(staging: Path, key: str) -> dict:
    with sqlite3.connect(staging) as connection:
        (metadata,) = connection.execute(
            "SELECT metadata FROM nodes WHERE key = ? AND parent = "
            "(SELECT id FROM nodes WHERE parent IS NULL)",
            (key,),
        ).fetchone()
    return json.loads(metadata)


def _lines(path: Path) -> list[dict]:
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines()]


def _stage(tmp_path: Path, marker: Path, **options):
    staging = tmp_path / "staging.db"
    options.setdefault("roots", [tmp_path])
    return staging, write_staging([str(marker)], staging, **options)


def _patch_describe(monkeypatch, change) -> None:
    original = ToyReader.describe

    def describe(self, resource):
        return change(original(self, resource))

    monkeypatch.setattr(ToyReader, "describe", describe)


def _replace_part(name: str, **updates):
    def change(descriptor: DataDescriptor) -> DataDescriptor:
        parts = [
            part.model_copy(update=updates) if part.name == name else part
            for part in descriptor.parts
        ]
        return DataDescriptor(parts=parts, payload=descriptor.payload)

    return change


def test_array_part_registered(tmp_path: Path, profile) -> None:
    marker = write_record(tmp_path / "rec", RECORD, stats=False, notes=False)
    staging, result = _stage(tmp_path, marker)
    assert result.staged == 1
    assert _children(staging, "rec-parts") == {"frames": "array"}
    structure = _structure(staging, "rec-parts", "frames")
    assert structure["shape"] == [4, 3]
    [frames] = _row(staging, "rec-parts")["tegel"]["parts"]
    assert frames["served_by"] == "tiled"
    assert frames["shape"] == [4, 3]
    assert frames["chunks"] == [[2, 2], [3]]
    assert frames["data_type"] == "<i4"
    assert "content_url" not in frames


def test_table_part_registered(tmp_path: Path, profile) -> None:
    marker = write_record(tmp_path / "rec", RECORD, frames=False, notes=False)
    staging, _ = _stage(tmp_path, marker)
    assert _children(staging, "rec-parts") == {"stats": "table"}
    [stats] = _row(staging, "rec-parts")["tegel"]["parts"]
    assert stats["served_by"] == "tiled"
    assert stats["columns"] == ["x", "y"]


def test_text_part_is_tegel_served(tmp_path: Path, profile) -> None:
    marker = write_record(tmp_path / "rec", RECORD, frames=False, stats=False)
    staging, _ = _stage(tmp_path, marker)
    assert _children(staging, "rec-parts") == {}
    [notes] = _row(staging, "rec-parts")["tegel"]["parts"]
    assert notes["served_by"] == "tegel"
    assert notes["size"] == (tmp_path / "rec" / "notes.txt").stat().st_size


def test_family_mismatch_is_parts_error(tmp_path: Path, profile, monkeypatch) -> None:
    _patch_describe(monkeypatch, _replace_part("frames", family=StructureFamily.table))
    marker = write_record(tmp_path / "rec", RECORD)
    log = tmp_path / "errors.jsonl"
    _, result = _stage(tmp_path, marker, error_log=log)
    assert (result.staged, result.skipped) == (0, 1)
    [line] = _lines(log)
    assert line["stage"] == "parts"
    assert "part frames" in line["message"]
    assert "as array, the part declares table" in line["message"]


def test_shape_hint_mismatch_is_parts_error(tmp_path: Path, profile, monkeypatch) -> None:
    _patch_describe(monkeypatch, _replace_part("frames", shape=[5, 3]))
    marker = write_record(tmp_path / "rec", RECORD)
    log = tmp_path / "errors.jsonl"
    _stage(tmp_path, marker, error_log=log)
    [line] = _lines(log)
    assert line["stage"] == "parts"
    assert "[5, 3]" in line["message"] and "[4, 3]" in line["message"]


def test_describe_raises_is_parts_error(tmp_path: Path, profile, monkeypatch) -> None:
    original = ToyReader.describe

    def describe(self, resource):
        if resource.id == "rec-bad":
            raise RuntimeError("cannot read header")
        return original(self, resource)

    monkeypatch.setattr(ToyReader, "describe", describe)
    bad = write_record(tmp_path / "bad", {**RECORD, "uuid": "rec-bad"})
    good = write_record(tmp_path / "good", RECORD)
    log = tmp_path / "errors.jsonl"
    staging = tmp_path / "staging.db"
    result = write_staging([str(bad), str(good)], staging, error_log=log, roots=[tmp_path])
    assert (result.staged, result.skipped) == (1, 1)
    [line] = _lines(log)
    assert line["stage"] == "parts"
    assert line["path"] == str(bad)
    assert "cannot read header" in line["message"]
    assert set(_children(staging, "rec-parts")) == {"frames", "stats"}


def test_fail_mode_on_parts_error(tmp_path: Path, profile, monkeypatch) -> None:
    _patch_describe(monkeypatch, _replace_part("frames", shape=[5, 3]))
    marker = write_record(tmp_path / "rec", RECORD)
    staging = tmp_path / "staging.db"
    with pytest.raises(StagingError) as caught:
        write_staging([str(marker)], staging, on_error="fail", roots=[tmp_path])
    assert caught.value.stage == "parts"
    assert not staging.exists()
    assert not Path(f"{staging}-wal").exists()


def test_part_outside_readable_storage_is_parts_error(tmp_path: Path, profile) -> None:
    marker = write_record(tmp_path / "rec", RECORD, stats=False, notes=False)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    log = tmp_path / "errors.jsonl"
    _, result = _stage(tmp_path, marker, error_log=log, roots=[elsewhere])
    assert result.skipped == 1
    [line] = _lines(log)
    assert line["stage"] == "parts"
    assert str(tmp_path / "rec" / "frames.zarr") in line["message"]
    assert str(elsewhere) in line["message"]


def test_old_reader_descriptor_fails_loudly(tmp_path: Path, profile, monkeypatch) -> None:
    monkeypatch.setattr(
        ToyReader, "describe", lambda self, resource: {"family": "bytes", "media_type": "x"}
    )
    marker = write_record(tmp_path / "rec", RECORD)
    log = tmp_path / "errors.jsonl"
    _stage(tmp_path, marker, error_log=log)
    [line] = _lines(log)
    assert line["stage"] == "parts"
    assert "invalid descriptor" in line["message"]


def test_readable_roots_from_env() -> None:
    assert readable_roots({"TEGEL_RECORDS": "/records"}) == [Path("/records")]
    both = {"TEGEL_READABLE_STORAGE": "/a:/b", "TEGEL_RECORDS": "/records"}
    assert readable_roots(both) == [Path("/a"), Path("/b")]
    assert readable_roots({}) == []
    with pytest.raises(RuntimeError, match="absolute"):
        readable_roots({"TEGEL_READABLE_STORAGE": "relative"})
