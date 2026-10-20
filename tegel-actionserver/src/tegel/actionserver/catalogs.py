"""The two catalogs the action server writes: results (served) and staging (internal).

Both are written through in-process Tiled clients that hold a random key and
are never exposed. One lock serializes writes to each catalog.
"""

from __future__ import annotations

import secrets
import threading
from typing import Any

from tegel.actionserver.config import ActionServerConfig
from tegel.dataserver.catalog import open_catalog


class Catalogs:
    def __init__(self, config: ActionServerConfig) -> None:
        for directory in (config.results_dir, config.outputs_dir, config.landing_dir,
                          config.work_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._storage = config.served_storage()
        self._staging_uri = config.staging_catalog_uri
        # Opening a Tiled catalog takes seconds; each is opened once and shared by the
        # served app and the writer client.
        self.results_tree = open_catalog(config.results_catalog_uri, self._storage)
        self.results_lock = threading.Lock()
        self.staging_lock = threading.RLock()
        self._results: Any = None
        self._staging: Any = None

    @property
    def results(self) -> Any:
        """Writer client on the results catalog."""
        if self._results is None:
            self._results = _client(self.results_tree)
        return self._results

    @property
    def staging(self) -> Any:
        """Client on the staging catalog, where binds land."""
        with self.staging_lock:
            if self._staging is None:
                self._staging = _client(open_catalog(self._staging_uri, self._storage))
        return self._staging


def _client(tree: Any) -> Any:
    from tiled.client import Context, from_context
    from tiled.config import Authentication
    from tiled.server.app import build_app

    key = secrets.token_hex(32)
    app = build_app(tree, authentication=Authentication(single_user_api_key=key))
    return from_context(Context.from_app(app, api_key=key))
