"""Profile slots are checked before the process launches."""

from pathlib import Path

import pytest

from tegel.dataserver.spec import load_spec

PROFILE = Path(__file__).resolve().parents[3] / "examples" / "toy" / "schema" / "profile.yaml"


def test_toy_profile_matches_its_schema() -> None:
    profile, schema = load_spec(PROFILE)
    assert profile.search == ["channel"]
    assert "channel" in schema["classes"]["Record"]["attributes"]


def test_unknown_profile_slot_is_rejected(tmp_path: Path) -> None:
    schema = tmp_path / "record.yaml"
    schema.write_text(
        "id: https://w3id.org/tegel/toy/record\n"
        "classes:\n"
        "  Record:\n"
        "    tree_root: true\n"
        "    attributes:\n"
        "      sample_name: {range: string}\n"
        "      uuid: {range: string}\n"
        "      recorded: {range: string}\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text(
        "id: https://w3id.org/tegel/toy/profile\n"
        "name: toy records\n"
        "schema: record.yaml\n"
        "search: [missing]\n"
        "properties: []\n"
        "entity:\n"
        "  name: sample_name\n"
        "  identifier: uuid\n"
        "  dateCreated: recorded\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing"):
        load_spec(profile)
