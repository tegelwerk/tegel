"""Process entry point."""

from __future__ import annotations

import os

import uvicorn

from tegel.dataserver.app import create_app
from tegel.dataserver.config import ServerConfig


def root_path() -> str | None:
    """Return ``TEGEL_ROOT_PATH`` when the process is mounted under a prefix."""
    value = os.environ.get("TEGEL_ROOT_PATH", "").strip()
    return value or None


def main() -> None:
    """Read ``TEGEL_*`` configuration and serve the modified Tiled process."""
    config = ServerConfig.from_env()
    options: dict[str, object] = {"host": config.host, "port": config.port}
    prefix = root_path()
    if prefix:
        options["root_path"] = prefix
    uvicorn.run(create_app(config), **options)
