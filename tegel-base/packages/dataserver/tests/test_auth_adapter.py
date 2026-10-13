"""One auth provider is handed to Tiled. Keycloak is the default."""

from pathlib import Path

from fastapi.testclient import TestClient

from tegel.core.contracts.capabilities import Principal
from tegel.dataserver.app import create_app
from tegel.dataserver.auth.adapter import TiledAuthAdapter
from tegel.dataserver.auth.keycloak import AuthenticationError, KeycloakAuth
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.testing import OIDCIssuer

PROFILE = Path(__file__).resolve().parents[3] / "examples" / "toy" / "schema" / "profile.yaml"


class StaticAuth:
    """Bearer token ``static-token`` and nothing else."""

    name = "static"

    def authenticate(self, authorization: str | None) -> Principal:
        if authorization == "Bearer static-token":
            return Principal(subject="reader")
        raise AuthenticationError("Authentication required")


def test_keycloak_is_the_only_tiled_authenticator(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.db"
    with OIDCIssuer() as issued:
        app = create_app(_config(catalog, issued))
        adapter = app.state.tegel_tiled_auth
        assert isinstance(adapter, TiledAuthAdapter)
        assert adapter.provider is app.state.tegel_auth
        assert isinstance(adapter.provider, KeycloakAuth)
        assert adapter.provider.name == "keycloak"
        with TestClient(app) as client:
            denied = client.get("/api/v1/metadata/root")
            assert denied.status_code == 401, denied.text
            accepted = client.get(
                "/api/v1/metadata/root",
                headers={"Authorization": f"Bearer {issued.token}"},
            )
            assert accepted.status_code != 401, accepted.text


def test_a_replacement_provider_is_the_only_authenticator(
    tmp_path: Path, monkeypatch
) -> None:
    catalog = tmp_path / "catalog.db"
    provider = StaticAuth()
    monkeypatch.setattr("tegel.dataserver.app.build_auth", lambda registry, config: provider)
    with OIDCIssuer() as issued:
        app = create_app(
            _config(catalog, issued).model_copy(update={"auth_name": "static"})
        )
        adapter = app.state.tegel_tiled_auth
        assert isinstance(adapter, TiledAuthAdapter)
        assert adapter.provider is provider
        with TestClient(app) as client:
            tiled = client.get(
                "/api/v1/metadata/root",
                headers={"Authorization": "Bearer static-token"},
            )
            read = client.get("/capabilities", headers={"Authorization": "Bearer static-token"})
            keycloak = client.get(
                "/api/v1/metadata/root",
                headers={"Authorization": f"Bearer {issued.token}"},
            )
            assert tiled.status_code != 401, tiled.text
            assert read.status_code == 200
            assert read.json()["auth"] == "static"
            assert keycloak.status_code == 401, keycloak.text


def _config(catalog: Path, issued: object) -> ServerConfig:
    return ServerConfig(
        public_origin="http://test",
        catalog_uri=f"sqlite:///{catalog}",
        profile_path=PROFILE,
        issuer_url=issued.issuer,  # type: ignore[attr-defined]
        audience=issued.audience,  # type: ignore[attr-defined]
        client_id=issued.client_id,  # type: ignore[attr-defined]
        client_secret=issued.client_secret,  # type: ignore[attr-defined]
        session_secret="test-session-secret",
    )
