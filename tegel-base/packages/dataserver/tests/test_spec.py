"""Profile fields are checked against the external schema before the process launches."""

from pathlib import Path

import pytest

from tegel.dataserver.config import ServerConfig
from tegel.dataserver.spec import load_spec

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
PROFILE = EXAMPLES / "toy" / "schema" / "profile.yaml"
SCHEMA_ROOT = EXAMPLES / "schemas"


def test_toy_profile_matches_its_external_schema() -> None:
    profile, schema = load_spec(PROFILE, SCHEMA_ROOT)
    assert profile.class_name == "Record"
    assert profile.schema_path == "toy/record.yaml"
    assert profile.search == ["channel"]
    assert "channel" in schema["classes"]["Record"]["attributes"]


def test_unknown_profile_field_is_rejected(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "id: https://w3id.org/tegel/toy/profile\n"
        "name: toy records\n"
        "schema_id: https://w3id.org/tegel/toy/record\n"
        'schema_version: "1"\n'
        "schema_path: toy/record.yaml\n"
        "class: Record\n"
        "search: [missing]\n"
        "properties: []\n"
        "entity:\n"
        "  name: sample_name\n"
        "  identifier: uuid\n"
        "  dateCreated: recorded\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing"):
        load_spec(profile, SCHEMA_ROOT)


def test_schema_root_is_required_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "TEGEL_PUBLIC_ORIGIN": "http://test",
        "TEGEL_CATALOG_URI": "sqlite:///catalog.db",
        "TEGEL_PROFILE": str(PROFILE),
        "TEGEL_OIDC_ISSUER": "http://issuer",
        "TEGEL_OIDC_AUDIENCE": "tegel",
        "TEGEL_OIDC_CLIENT_ID": "tegel",
        "TEGEL_SESSION_SECRET": "secret",
    }.items():
        monkeypatch.setenv(name, value)
    assert ServerConfig.from_env().schema_root == SCHEMA_ROOT
    monkeypatch.delenv("TEGEL_SCHEMA_ROOT")
    with pytest.raises(ValueError, match="TEGEL_SCHEMA_ROOT"):
        ServerConfig.from_env()
