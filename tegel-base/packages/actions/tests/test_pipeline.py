"""Staging, merge, credentials, and the public redirect URI."""

import sqlite3
from pathlib import Path

from tegel.actions.credentials import ensure_credentials
from tegel.actions.merge import merge_staging
from tegel.actions.realm import redirect_uris
from tegel.actions.staging import catalog_key, claim_paths, write_staging
from tegel.core.plugins import PluginContractError

PROFILE = Path(__file__).resolve().parents[3] / "examples" / "toy" / "schema" / "profile.yaml"


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


def test_catalog_key_rejects_a_slash() -> None:
    try:
        catalog_key({"key": "a/b"})
    except ValueError as exc:
        assert "one path segment" in str(exc)
    else:
        raise AssertionError("a slash in the key must be rejected")


def test_claimed_record_is_merged_into_production(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    raw = tmp_path / "records" / "note.npy"
    raw.parent.mkdir()
    raw.write_bytes(b"not-read")
    raw.with_suffix(".json").write_text(
        '{"uuid": "rec-note", "sample_name": "note", "recorded": "2026-01-01", "channel": "bin"}',
        encoding="utf-8",
    )
    accepted = claim_paths(raw.parent)
    assert accepted == [str(raw)]
    staging = tmp_path / "staging.db"
    assert write_staging(accepted, staging) == 1
    production = tmp_path / "production.db"
    from tiled.catalog import from_uri

    from_uri(f"sqlite:///{production.resolve()}", init_if_not_exists=True)
    merge_staging(str(staging), str(production))
    assert not staging.exists()
    with sqlite3.connect(production) as connection:
        row = connection.execute(
            "SELECT key FROM nodes WHERE key = ?",
            ("rec-note",),
        ).fetchone()
    assert row == ("rec-note",)


def test_staging_rejects_a_key_the_class_lacks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    raw = tmp_path / "records" / "note.npy"
    raw.parent.mkdir()
    raw.write_bytes(b"not-read")
    raw.with_suffix(".json").write_text(
        '{"uuid": "rec-note", "exposure_count": "7"}',
        encoding="utf-8",
    )
    try:
        write_staging([str(raw)], tmp_path / "staging.db")
    except PluginContractError as exc:
        assert "exposure_count" in str(exc)
    else:
        raise AssertionError("a metadata key that is not a class slot must be rejected")
