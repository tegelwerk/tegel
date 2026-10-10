"""Process entry point."""

from __future__ import annotations

import uvicorn

from tegel.dataserver.app import create_app
from tegel.dataserver.config import ServerConfig


def main() -> None:
    """Read ``TEGEL_*`` configuration and serve the modified Tiled process."""
    config = ServerConfig.from_env()
    uvicorn.run(create_app(config), host=config.host, port=config.port)
