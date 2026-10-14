"""Profile fields are checked against the external schema before the process launches."""

from pathlib import Path

import pytest

from tegel.dataserver.app import create_app
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.spec import load_spec, record_slots

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
PROFILE = EXAMPLES / "toy" / "schema" / "profile.yaml"
SCHEMA_ROOT = EXAMPLES / "schemas"


def test_toy_profile_matches_its_external_schema() -> None:
    profile, schema = load_spec(PROFILE, SCHEMA_ROOT)
    assert profile.class_name == "Record"
    assert profile.schema_path == "toy/record.yaml"
    assert profile.search == ["channel", "calibrated"]
    assert record_slots(profile, schema) == {
        "recorded",
        "sample_name",
        "uuid",
        "channel",
        "calibrated",
    }


def test_startup_refuses_schema_mismatch(tmp_path: Path) -> None:
    schemas = tmp_path / "schemas"
    (schemas / "toy").mkdir(parents=True)
    text = (SCHEMA_ROOT / "toy" / "record.yaml").read_text(encoding="utf-8")
    (schemas / "toy" / "record.yaml").write_text(
        text.replace("https://w3id.org/tegel/toy/record", "https://example.com/other"),
        encoding="utf-8",
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        PROFILE.read_text(encoding="utf-8").replace(
            "https://w3id.org/tegel/toy/record", "https://example.com/other"
        ),
        encoding="utf-8",
    )
    config = ServerConfig(
        public_origin="http://test",
        catalog_uri=f"sqlite:///{tmp_path / 'catalog.db'}",
        profile_path=profile,
        schema_root=schemas,
        issuer_url="http://issuer",
        audience="tegel",
        client_id="tegel",
        client_secret="",
        session_secret="secret",
    )
    with pytest.raises(RuntimeError) as caught:
        create_app(config)
    assert str(caught.value) == (
        "resource type toy.record uses schema https://w3id.org/tegel/toy/record; "
        "the profile names https://example.com/other"
    )
    assert not (tmp_path / "catalog.db").exists()


def test_unknown_profile_field_is_rejected(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "id: https://w3id.org/tegel/toy/profile\n"
        "name: toy records\n"
        "schema_id: https://w3id.org/tegel/toy/record\n"
        'schema_version: "2"\n'
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
