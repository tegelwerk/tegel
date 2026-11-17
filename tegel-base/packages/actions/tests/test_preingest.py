"""Pre-ingest: converters turn ready raw files into records the pipeline ingests."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tegel.actions import ingest, preingest
from tegel.core.plugins import Registry
from tegel.toy.dataserver.fixtures import write_record

PROFILE = Path(__file__).resolve().parents[3] / "examples" / "toy" / "schema" / "profile.yaml"


class FakeConverter:
    """Each ``*.raw`` file becomes one toy record under <collection>/<stem>/."""

    plugin_id = "fake-raw-v1"
    patterns = ("*.raw",)

    def group(self, paths):
        return [[path] for path in sorted(paths)]

    def convert(self, paths, records, options):
        (path,) = paths
        if path.read_bytes() == b"bad":
            raise ValueError("unreadable raw file")
        folder = Path(records) / (options["collection"] or "_none") / path.stem
        record = {"uuid": f"raw-{path.stem}", "sample_name": path.stem,
                  "recorded": "2026-01-01", "channel": options.get("channel", "bin"),
                  "calibrated": True}
        return [write_record(folder, record, frames=False, stats=False, notes=False)]


class Escaping(FakeConverter):
    def convert(self, paths, records, options):
        return [write_record(Path(records).parent / "elsewhere", {"uuid": "x"},
                             frames=False, stats=False, notes=False)]


def _registry(converter=FakeConverter) -> Registry:
    return Registry({"tegel.converters": {"fake": converter}})


def _touch(path: Path, content: bytes = b"x", age: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
    return path


def test_ready_files_are_converted_once(tmp_path: Path) -> None:
    raw, records, output = tmp_path / "raw", tmp_path / "records", tmp_path / "out"
    _touch(raw / "aapl" / "a.raw")
    loose = _touch(raw / "b.raw")
    _touch(raw / "notes.txt")
    _touch(raw / ".hidden" / "c.raw")
    result = preingest.convert_ready([raw], records, output, stable=0, registry=_registry(),
                                     config={"fake": {"channel": "flat"}})
    assert sorted(item["collection"] or "" for item in result.converted) == ["", "aapl"]
    assert sorted(p.parent.name for p in result.records) == ["a", "b"]
    assert (records / "aapl" / "a" / "record.json").is_file()
    assert '"channel": "flat"' in (records / "aapl" / "a" / "record.json").read_text()
    assert preingest.convert_ready([raw], records, output, stable=0,
                                   registry=_registry()).converted == []
    _touch(loose, b"changed")
    assert preingest.convert_ready([raw], records, output, stable=0,
                                   registry=_registry()).converted == []


def test_files_still_being_written_wait(tmp_path: Path) -> None:
    fresh = _touch(tmp_path / "raw" / "a.raw")
    ledger = preingest.Ledger(tmp_path / "ledger.json")
    converters = _registry().converters
    assert preingest.pending([tmp_path / "raw"], converters, ledger, stable=60) == []
    later = preingest.pending([tmp_path / "raw"], converters, ledger, stable=60,
                              now=fresh.stat().st_mtime + 61)
    assert [unit.files for unit in later] == [[fresh]]


def test_failures_are_recorded_and_retried_after_a_change(tmp_path: Path) -> None:
    raw, records, output = tmp_path / "raw", tmp_path / "records", tmp_path / "out"
    bad = _touch(raw / "a.raw", b"bad", age=5)
    first = preingest.convert_ready([raw], records, output, stable=0, registry=_registry())
    assert first.failed and "unreadable" in first.failed[0]["error"]
    assert preingest.convert_ready([raw], records, output, stable=0,
                                   registry=_registry()).failed == []
    _touch(bad, b"fixed")
    assert len(preingest.convert_ready([raw], records, output, stable=0,
                                       registry=_registry()).converted) == 1


def test_records_must_stay_under_the_records_directory(tmp_path: Path) -> None:
    _touch(tmp_path / "raw" / "a.raw")
    result = preingest.convert_ready([tmp_path / "raw"], tmp_path / "records", tmp_path / "out",
                                     stable=0, registry=_registry(Escaping))
    assert "outside" in result.failed[0]["error"]


def test_raw_roots_must_be_absolute(monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_RAW", "relative/raw")
    with pytest.raises(RuntimeError, match="absolute"):
        preingest.raw_roots()


def test_ingest_raw_flow_converts_and_commits_only_new_records(tmp_path: Path,
                                                               monkeypatch) -> None:
    committed: list[dict] = []

    class Done:
        class state:
            @staticmethod
            def is_completed() -> bool:
                return True

    def run_deployment(**kwargs):
        committed.append(kwargs["parameters"])
        return Done()

    raw, records, output = tmp_path / "raw", tmp_path / "records", tmp_path / "out"
    _touch(raw / "aapl" / "a.raw")
    _touch(raw / "b.raw")
    write_record(records / "existing", {"uuid": "old"}, frames=False, stats=False, notes=False)
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    monkeypatch.setenv("TEGEL_OUTPUT", str(output))
    monkeypatch.setenv("TEGEL_RECORDS", str(records))
    monkeypatch.setenv("TEGEL_RAW", str(raw))
    monkeypatch.delenv("TEGEL_ON_ERROR", raising=False)
    monkeypatch.setattr(ingest, "Registry", _registry)
    monkeypatch.setattr(ingest, "run_deployment", run_deployment)
    monkeypatch.setattr(ingest, "refresh_catalog", lambda: None)
    # call the task bodies directly: no Prefect API is needed for this test
    monkeypatch.setattr(ingest, "_find_ready", ingest._find_ready.fn)
    monkeypatch.setattr(ingest, "_convert", ingest._convert.fn)
    monkeypatch.setattr(ingest, "_run_id", lambda: "test-run")
    reports: list = []
    monkeypatch.setattr(ingest, "_report_raw", lambda *args: reports.append(args))
    assert ingest.ingest_raw.fn(stable=0) == 2
    assert len(committed) == 1 and committed[0]["production_path"].endswith("catalog.db")
    assert ingest.ingest_raw.fn(stable=0) == 0  # an idle pass is not an error
    assert len(committed) == 1
    assert len(reports) == 1  # the idle pass publishes nothing
