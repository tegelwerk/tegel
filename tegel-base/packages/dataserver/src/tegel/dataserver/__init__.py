"""Reference data server."""

from tegel.dataserver.app import create_app
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.version import __version__

__all__ = ["ServerConfig", "__version__", "create_app"]
