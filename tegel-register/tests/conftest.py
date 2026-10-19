"""In-process Tiled server whose readable storage is a symlink view of the bytes."""
from __future__ import annotations

from pathlib import Path

import pytest

from tegel.register.site import Site


@pytest.fixture
def tiled_site(tmp_path: Path):
    """Bytes live in ``real``. The server reads them through the ``srvview`` symlink."""
    pytest.importorskip("tiled.server")
    from tiled.client import from_uri
    from tiled.server import SimpleTiledServer

    real = tmp_path / "real"
    real.mkdir()
    srvview = tmp_path / "srvview"
    srvview.symlink_to(real, target_is_directory=True)
    server = SimpleTiledServer(directory=tmp_path / "catalog", readable_storage=[srvview])
    client = from_uri(server.uri)
    site = Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(real), "server": str(srvview)}],
            "target": {"base_uri": server.uri.split("?", 1)[0]},
        }
    )
    try:
        yield client, site, real, srvview, server
    finally:
        server.close()
