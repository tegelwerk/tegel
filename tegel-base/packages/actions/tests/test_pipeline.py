"""Staging, merge, credentials, and the public redirect URI."""

import json
import logging
import sqlite3
from pathlib import Path

import pytest

from tegel.actions import ingest
from tegel.actions.credentials import ensure_credentials
from tegel.actions.merge import merge_staging
from tegel.actions.realm import redirect_uris
from tegel.actions.staging import StagingError, claim_paths, write_staging
from tegel.core.contracts.types import ResourceType
from tegel.core.plugins import Registry
from tegel.toy.dataserver import ToyExtractor
from tegel.toy.dataserver.fixtures import write_record

PROFILE = Path(__file__).resolve().parents[3] / "examples" / "toy" / "schema" / "profile.yaml"
SIDECAR = {
    "uuid": "rec-note",
    "sample_name": "note",
    "recorded": "2026-01-01",
    "channel": "bin",
    "calibrated": True,
}
LOG_KEYS = {
    "time",
    "run_id",
    "mode",
    "stage",
    "path",
    "plugin_id",
    "resource_id",
    "error",
    "message",
}


def _record(tmp_path: Path, sidecar: dict, name: str = "note") -> Path:
    folder = tmp_path / "records" / name
    return write_record(folder, sidecar, frames=False, stats=False, notes=False)


def _three(tmp_path: Path) -> list[str]:
    """Three records; the middle one has a key that is not a class slot."""
    return [
        str(_record(tmp_path, {**SIDECAR, "uuid": "rec-a"}, "a")),
        str(_record(tmp_path, {"uuid": "rec-b", "exposure_count": "7"}, "b")),
        str(_record(tmp_path, {**SIDECAR, "uuid": "rec-c"}, "c")),
    ]


def _stored(path: Path, key: str) -> dict | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT metadata FROM nodes WHERE key = ?", (key,)).fetchone()
    return None if row is None else json.loads(row[0])


def _lines(path: Path) -> list[dict]:
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def profile(monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))


def test_redirect_uris_follow_the_public_data_prefix() -> None:
    assert redirect_uris("https://xxx.yy.gov:8082", "/data") == [
        "https://xxx.yy.gov:8082/data/*"
    ]


def test_credentials_are_written_once(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_OUTPUT", str(tmp_path))
    from tegel.actions.credentials import credential_path

    first = ensure_credentials(credential_path())
    second = ensure_credentials(credential_path())
    assert first["admin_password"] == second["admin_password"]
    assert credential_path().stat().st_mode & 0o777 == 0o600


def test_staging_writes_two_blocks(tmp_path: Path, profile) -> None:
    raw = _record(tmp_path, SIDECAR)
    staging = tmp_path / "staging.db"
    assert write_staging([str(raw)], staging).staged == 1
    stored = _stored(staging, "rec-note")
    assert stored is not None
    assert stored["record"] == SIDECAR
    assert stored["tegel"] == {
        "plugin_id": "toy-record",
        "source_name": "record.json",
        "resource_type": "toy.record",
        "resource_type_version": "1",
        "payload_uri": raw.resolve().as_uri(),
        "relations": [],
        "schema_id": "https://w3id.org/tegel/toy/record",
        "schema_version": "2",
        "class": "Record",
        "parts": [],
        "payload": {
            "name": "payload",
            "family": "bytes",
            "media_type": "application/json",
            "asset_uri": raw.resolve().as_uri(),
            "designated": False,
            "size": raw.stat().st_size,
            "served_by": "tegel",
        },
    }


def test_claimed_record_is_merged_into_production(tmp_path: Path, profile) -> None:
    raw = _record(tmp_path, SIDECAR)
    accepted = claim_paths(raw.parent.parent)
    assert accepted == [str(raw)]
    staging = tmp_path / "staging.db"
    assert write_staging(accepted, staging).staged == 1
    production = tmp_path / "production.db"
    from tiled.catalog import from_uri

    from_uri(f"sqlite:///{production.resolve()}", init_if_not_exists=True)
    merge_staging(str(staging), str(production))
    assert not staging.exists()
    stored = _stored(production, "rec-note")
    assert stored is not None
    assert set(stored) == {"record", "tegel"}


def test_staging_rejects_record_key_outside_class(tmp_path: Path, profile) -> None:
    raw = _record(tmp_path, {"uuid": "rec-note", "exposure_count": "7"})
    staging = tmp_path / "staging.db"
    with pytest.raises(StagingError, match="record keys are not class slots: exposure_count"):
        write_staging([str(raw)], staging, on_error="fail")
    assert not staging.exists()


def test_staging_rejects_an_identifier_with_a_slash(tmp_path: Path, profile) -> None:
    raw = _record(tmp_path, {**SIDECAR, "uuid": "a/b"})
    with pytest.raises(StagingError, match="single path segment"):
        write_staging([str(raw)], tmp_path / "staging.db", on_error="fail")


def test_skip_mode_stages_good_records(tmp_path: Path, profile) -> None:
    paths = _three(tmp_path)
    staging = tmp_path / "staging.db"
    log = tmp_path / "ingest-errors.jsonl"
    result = write_staging(paths, staging, error_log=log, run_id="run-1")
    assert (result.staged, result.skipped) == (2, 1)
    assert result.errors[0].path == paths[1]
    assert _stored(staging, "rec-a") is not None
    assert _stored(staging, "rec-c") is not None
    [line] = _lines(log)
    assert line["stage"] == "check"
    assert line["mode"] == "skip"
    assert line["path"] == paths[1]
    assert line["plugin_id"] == "toy-record"
    assert line["resource_id"] is None
    assert line["error"] == "PluginContractError"
    assert "exposure_count" in line["message"]


def test_fail_mode_raises_and_logs(tmp_path: Path, profile) -> None:
    paths = _three(tmp_path)
    staging = tmp_path / "staging.db"
    log = tmp_path / "ingest-errors.jsonl"
    with pytest.raises(StagingError) as caught:
        write_staging(paths, staging, on_error="fail", error_log=log)
    assert caught.value.path == paths[1]
    assert paths[1] in str(caught.value)
    assert "exposure_count" in str(caught.value)
    [line] = _lines(log)
    assert line["mode"] == "fail"
    assert line["stage"] == "check"
    assert not staging.exists()


def test_process_exception_is_a_record_error(tmp_path: Path, profile, monkeypatch) -> None:
    def boom(self, input_path, options):
        raise RuntimeError("boom")

    monkeypatch.setattr(ToyExtractor, "process", boom)
    raw = _record(tmp_path, SIDECAR)
    log = tmp_path / "ingest-errors.jsonl"
    result = write_staging([str(raw)], tmp_path / "staging.db", error_log=log)
    assert result.skipped == 1
    [line] = _lines(log)
    assert line["stage"] == "process"
    assert line["error"] == "RuntimeError"
    assert line["message"] == "boom"


def test_missing_id_is_logged(tmp_path: Path, profile) -> None:
    raw = _record(tmp_path, {**SIDECAR, "uuid": None})
    log = tmp_path / "ingest-errors.jsonl"
    result = write_staging([str(raw)], tmp_path / "staging.db", error_log=log)
    assert result.staged == 0
    [line] = _lines(log)
    assert line["stage"] == "id"
    assert line["resource_id"] is None


def test_duplicate_id_in_batch_is_logged(tmp_path: Path, profile) -> None:
    first = _record(tmp_path, {**SIDECAR, "uuid": "rec-same"}, "first")
    second = _record(tmp_path, {**SIDECAR, "uuid": "rec-same", "channel": "other"}, "second")
    staging = tmp_path / "staging.db"
    log = tmp_path / "ingest-errors.jsonl"
    result = write_staging([str(first), str(second)], staging, error_log=log)
    assert (result.staged, result.skipped) == (1, 1)
    assert _stored(staging, "rec-same")["record"]["channel"] == "bin"
    [line] = _lines(log)
    assert line["stage"] == "duplicate"
    assert line["path"] == str(second)
    assert line["resource_id"] == "rec-same"


def test_schema_mismatch_fails_in_both_modes(tmp_path: Path, profile, monkeypatch) -> None:
    other = ResourceType(name="toy.record", version="1", metadata_schema="https://example.org/other")
    monkeypatch.setattr(Registry, "resource_types", lambda self: {"toy.record": other})
    raw = _record(tmp_path, SIDECAR)
    log = tmp_path / "ingest-errors.jsonl"
    for mode in ("skip", "fail"):
        with pytest.raises(RuntimeError, match="uses schema https://example.org/other"):
            write_staging([str(raw)], tmp_path / "staging.db", on_error=mode, error_log=log)
    assert not log.exists()


def test_error_log_none_uses_logger_only(tmp_path: Path, profile, caplog) -> None:
    paths = _three(tmp_path)
    with caplog.at_level(logging.WARNING, logger="tegel.staging"):
        result = write_staging(paths, tmp_path / "staging.db", error_log=None)
    assert result.skipped == 1
    assert list(tmp_path.rglob("*.jsonl")) == []
    [warning] = [r for r in caplog.records if r.name == "tegel.staging"]
    assert warning.levelno == logging.WARNING
    assert "exposure_count" in warning.getMessage()


def test_log_line_shape(tmp_path: Path, profile, monkeypatch) -> None:
    def long(self, input_path, options):
        raise RuntimeError("first\nsecond " + "x" * 5000)

    paths = _three(tmp_path)
    log = tmp_path / "ingest-errors.jsonl"
    write_staging(paths, tmp_path / "one.db", error_log=log, run_id="run-1")
    monkeypatch.setattr(ToyExtractor, "process", long)
    write_staging(paths[:1], tmp_path / "two.db", error_log=log, run_id="run-1")
    lines = _lines(log)
    assert len(lines) == 2
    for line in lines:
        assert set(line) == LOG_KEYS
        assert "\n" not in line["message"]
        assert line["time"].endswith("Z") and len(line["time"]) == 20
    cut = lines[1]["message"]
    assert cut.startswith("first second ")
    assert len(cut) == 2001 and cut.endswith("…")


def _flow_env(tmp_path: Path, monkeypatch) -> None:
    class Done:
        class state:
            @staticmethod
            def is_completed() -> bool:
                return True

    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    monkeypatch.setenv("TEGEL_OUTPUT", str(tmp_path / "output"))
    monkeypatch.delenv("TEGEL_ON_ERROR", raising=False)
    monkeypatch.setattr(ingest, "run_deployment", lambda **kwargs: Done())
    monkeypatch.setattr(ingest, "refresh_catalog", lambda: None)


def test_run_id_shared_across_batches(tmp_path: Path, monkeypatch) -> None:
    _flow_env(tmp_path, monkeypatch)
    for name in ("a", "b", "c"):
        _record(tmp_path, {"uuid": f"rec-{name}", "exposure_count": "7"}, name)
    with pytest.raises(RuntimeError, match="no record"):
        ingest.ingest_directories.fn(records=str(tmp_path / "records"), batch_size=1)
    lines = _lines(tmp_path / "output" / "ingest-errors.jsonl")
    assert len(lines) == 3
    assert len({line["run_id"] for line in lines}) == 1


def test_all_skipped_message(tmp_path: Path, monkeypatch) -> None:
    _flow_env(tmp_path, monkeypatch)
    for name in ("a", "b", "c"):
        _record(tmp_path, {"uuid": f"rec-{name}", "exposure_count": "7"}, name)
    log = tmp_path / "output" / "ingest-errors.jsonl"
    records = tmp_path / "records"
    with pytest.raises(RuntimeError) as caught:
        ingest.ingest_directories.fn(records=str(records))
    assert str(caught.value) == f"no record staged from {records}: 3 skipped; errors in {log}"
    assert list((tmp_path / "output" / "staging").iterdir()) == []


def test_unexpected_error_discards_staging(tmp_path: Path, profile, monkeypatch) -> None:
    from tegel.actions import staging as staging_module

    def broken(record, profile):
        raise TypeError("not a stage error")

    monkeypatch.setattr(staging_module, "resource_id_for", broken)
    raw = _record(tmp_path, SIDECAR)
    staging = tmp_path / "staging" / "batch.db"
    with pytest.raises(TypeError, match="not a stage error"):
        write_staging([str(raw)], staging)
    assert sorted(path.name for path in staging.parent.iterdir()) == []


def test_invalid_on_error_value(tmp_path: Path, monkeypatch) -> None:
    _flow_env(tmp_path, monkeypatch)
    monkeypatch.setenv("TEGEL_ON_ERROR", "maybe")

    def no_claim(root):
        raise AssertionError("paths must not be claimed")

    monkeypatch.setattr(ingest, "claim_paths", no_claim)
    with pytest.raises(RuntimeError) as caught:
        ingest.ingest_directories.fn(records=str(tmp_path))
    message = str(caught.value)
    assert "TEGEL_ON_ERROR" in message
    assert "skip" in message and "fail" in message


def test_flow_summary_mentions_log(tmp_path: Path, monkeypatch, capsys) -> None:
    _flow_env(tmp_path, monkeypatch)
    log = tmp_path / "output" / "ingest-errors.jsonl"
    _three(tmp_path)
    assert ingest.ingest_directories.fn(records=str(tmp_path / "records")) == 2
    assert f"staged 2, skipped 1; errors in {log}" in capsys.readouterr().out

    (tmp_path / "records" / "b" / "record.json").unlink()
    assert ingest.ingest_directories.fn(records=str(tmp_path / "records")) == 2
    out = capsys.readouterr().out
    assert "staged 2, skipped 0" in out
    assert "errors in" not in out
