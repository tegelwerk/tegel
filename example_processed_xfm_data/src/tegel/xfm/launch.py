"""Start the reference server with this plugin and the sample record."""

from __future__ import annotations

import secrets
from pathlib import Path

import uvicorn

from tegel.core.plugins import check_package
from tegel.dataserver.app import create_app
from tegel.dataserver.catalog import insert_record
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.testing import OIDCIssuer

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "processed-leaf.json"
PROFILE = ROOT / "schema" / "profile.yaml"
CATALOG = ROOT / "var" / "catalog.db"
HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    """Load the plugin by entry point, catalog the sample, and serve it."""
    origin = f"http://{HOST}:{PORT}"
    _reset_catalog(CATALOG)
    with OIDCIssuer(lifetime=8 * 60 * 60) as issued:
        app = create_app(
            ServerConfig(
                public_origin=origin,
                catalog_uri=f"sqlite:///{CATALOG}",
                profile_path=PROFILE,
                issuer_url=issued.issuer,
                audience=issued.audience,
                client_id=issued.client_id,
                client_secret=issued.client_secret,
                session_secret=secrets.token_hex(32),
                host=HOST,
                port=PORT,
            )
        )
        plugin = app.state.tegel_registry.extractor_for(str(FIXTURE))
        package = check_package(plugin, plugin.process(str(FIXTURE), {}))
        key = str(package.metadata["key"])
        insert_record(
            app.state.tegel_catalog,
            key=key,
            metadata=package.metadata,
            structure_family="array",
        )
        token_path = CATALOG.parent / "token"
        token_path.write_text(issued.token + "\n", encoding="utf-8")
        print(f"Listening on {origin}")
        print(f"Plugin {plugin.plugin_id} catalogued {key}")
        print(f"Bearer token written to {token_path}")
        print(
            "curl -H "
            f'"Authorization: Bearer $(cat {token_path})" '
            f'"{origin}/search?sample_code=AAPL-P1-leaf1"'
        )
        uvicorn.run(app, host=HOST, port=PORT)


def _reset_catalog(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)
