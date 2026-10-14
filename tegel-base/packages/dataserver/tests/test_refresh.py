"""The refresh route reopens the catalog and does not write a record."""

from pathlib import Path

from fastapi.testclient import TestClient

from tegel.dataserver.app import create_app
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.testing import OIDCIssuer

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
PROFILE = EXAMPLES / "toy" / "schema" / "profile.yaml"
SCHEMA_ROOT = EXAMPLES / "schemas"


def test_refresh_requires_a_token_and_reopens_the_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    with OIDCIssuer() as issued:
        app = create_app(
            ServerConfig(
                public_origin="http://test",
                catalog_uri=f"sqlite:///{catalog}",
                profile_path=PROFILE,
                schema_root=SCHEMA_ROOT,
                issuer_url=issued.issuer,
                audience=issued.audience,
                client_id=issued.client_id,
                client_secret=issued.client_secret,
                session_secret="test-session-secret",
            )
        )
        with TestClient(app) as client:
            denied = client.post("/refresh-catalog")
            assert denied.status_code == 401
            assert denied.json()["detail"] == "Authentication required"
            accepted = client.post(
                "/refresh-catalog",
                headers={"Authorization": f"Bearer {issued.token}"},
            )
            assert accepted.status_code == 200
            assert accepted.json() == {"refreshed": True}
