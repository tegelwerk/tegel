"""The index serves {record, tegel} rows and skips any other row."""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tegel.core.version import SPEC_VERSION
from tegel.dataserver.app import create_app
from tegel.dataserver.catalog import insert_record
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.testing import OIDCIssuer

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
PROFILE = EXAMPLES / "toy" / "schema" / "profile.yaml"
SCHEMA_ROOT = EXAMPLES / "schemas"


def _app(tmp_path: Path, issued) -> object:
    return create_app(
        ServerConfig(
            public_origin="http://test",
            catalog_uri=f"sqlite:///{tmp_path / 'catalog.db'}",
            profile_path=PROFILE,
            schema_root=SCHEMA_ROOT,
            issuer_url=issued.issuer,
            audience=issued.audience,
            client_id=issued.client_id,
            client_secret=issued.client_secret,
            session_secret="test-session-secret",
        )
    )


def test_flat_row_is_skipped_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with OIDCIssuer() as issued:
        app = _app(tmp_path, issued)
        insert_record(
            app.state.tegel_catalog,
            key="old-row",
            metadata={"uuid": "old-row", "plugin_id": "toy-record", "source_name": "a.npy"},
            structure_family="bytes",
        )
        headers = {"Authorization": f"Bearer {issued.token}"}
        with TestClient(app) as client, caplog.at_level(logging.WARNING, "tegel.index"):
            page = client.get("/resources", headers=headers).json()
            assert page == {"resources": [], "count": 0}
            assert client.get("/resources/old-row", headers=headers).status_code == 404
    warnings = [record for record in caplog.records if record.name == "tegel.index"]
    assert len(warnings) == 1
    assert "old-row is not a Tegel 0.4 row and must be re-ingested" in warnings[0].getMessage()


def test_capabilities_reports_schema(tmp_path: Path) -> None:
    with OIDCIssuer() as issued:
        app = _app(tmp_path, issued)
        with TestClient(app) as client:
            body = client.get(
                "/capabilities", headers={"Authorization": f"Bearer {issued.token}"}
            ).json()
    assert body["spec_version"] == SPEC_VERSION
    assert body["schema_id"] == "https://w3id.org/tegel/toy/record"
    assert body["schema_version"] == "2"
    assert body["class"] == "Record"
