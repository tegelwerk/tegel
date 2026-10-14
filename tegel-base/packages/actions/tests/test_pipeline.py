"""Staging, merge, credentials, and the public redirect URI."""

import json
import sqlite3
from pathlib import Path

from tegel.actions.credentials import ensure_credentials
from tegel.actions.merge import merge_staging
from tegel.actions.realm import redirect_uris
from tegel.actions.staging import claim_paths, write_staging
from tegel.core.plugins import PluginContractError

PROFILE = Path(__file__).resolve().parents[3] / "examples" / "toy" / "schema" / "profile.yaml"
SIDECAR = {
    "uuid": "rec-note",
    "sample_name": "note",
    "recorded": "2026-01-01",
    "channel": "bin",
    "calibrated": True,
}


def _record(tmp_path: Path, sidecar: dict) -> Path:
    raw = tmp_path / "records" / "note.npy"
    raw.parent.mkdir()
    raw.write_bytes(b"not-read")
    raw.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")
    return raw


def _stored(path: Path, key: str) -> dict | None:
    with sqlite3.connect(path) as connection:
        row = connection.execute("SELECT metadata FROM nodes WHERE key = ?", (key,)).fetchone()
    return None if row is None else json.loads(row[0])


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


def test_staging_writes_two_blocks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    raw = _record(tmp_path, SIDECAR)
    staging = tmp_path / "staging.db"
    assert write_staging([str(raw)], staging) == 1
    stored = _stored(staging, "rec-note")
    assert stored is not None
    assert stored["record"] == SIDECAR
    assert stored["tegel"] == {
        "plugin_id": "toy-record",
        "source_name": "note.npy",
        "resource_type": "toy.record",
        "resource_type_version": "1",
        "payload_uri": raw.resolve().as_uri(),
        "relations": [],
        "schema_id": "https://w3id.org/tegel/toy/record",
        "schema_version": "2",
        "class": "Record",
    }


def test_claimed_record_is_merged_into_production(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    raw = _record(tmp_path, SIDECAR)
    accepted = claim_paths(raw.parent)
    assert accepted == [str(raw)]
    staging = tmp_path / "staging.db"
    assert write_staging(accepted, staging) == 1
    production = tmp_path / "production.db"
    from tiled.catalog import from_uri

    from_uri(f"sqlite:///{production.resolve()}", init_if_not_exists=True)
    merge_staging(str(staging), str(production))
    assert not staging.exists()
    stored = _stored(production, "rec-note")
    assert stored is not None
    assert set(stored) == {"record", "tegel"}


def test_staging_rejects_record_key_outside_class(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    raw = _record(tmp_path, {"uuid": "rec-note", "exposure_count": "7"})
    staging = tmp_path / "staging.db"
    try:
        write_staging([str(raw)], staging)
    except PluginContractError as exc:
        assert "record keys are not class slots: exposure_count" in str(exc)
    else:
        raise AssertionError("a record key that is not a class slot must be rejected")
    assert _stored(staging, "rec-note") is None


def test_staging_rejects_an_identifier_with_a_slash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    raw = _record(tmp_path, {**SIDECAR, "uuid": "a/b"})
    try:
        write_staging([str(raw)], tmp_path / "staging.db")
    except ValueError as exc:
        assert "single path segment" in str(exc)
    else:
        raise AssertionError("a slash in the catalog id must be rejected")
