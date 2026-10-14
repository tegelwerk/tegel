"""Read-only view of a Tiled catalog."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

from tegel.core.contracts.resources import Resource, resource_from_catalog, resource_uri
from tegel.core.contracts.types import SearchProfile
from tegel.dataserver.catalog import sqlite_path

_log = logging.getLogger("tegel.index")


class TiledCatalogIndex:
    """``get`` and ``keys`` over the catalog the ETL application committed.

    A no-argument constructor reads ``TEGEL_CATALOG_URI`` and
    ``TEGEL_PUBLIC_ORIGIN``. The launch process passes the open catalog's
    URI and the loaded profile directly. A row that is not ``{record, tegel}``
    is not served.
    """

    def __init__(
        self,
        catalog_uri: str | None = None,
        public_origin: str | None = None,
        profile: SearchProfile | None = None,
    ) -> None:
        self.catalog_uri = catalog_uri or os.environ["TEGEL_CATALOG_URI"]
        self.public_origin = public_origin or os.environ["TEGEL_PUBLIC_ORIGIN"]
        self.profile = profile
        self._warned: set[str] = set()

    def get(self, resource_id: str) -> Resource | None:
        stored = self._metadata(resource_id)
        if stored is None:
            return None
        try:
            return resource_from_catalog(
                resource_id,
                resource_uri(self.public_origin, resource_id),
                stored,
                profile=self.profile,
            )
        except ValueError as exc:
            if resource_id not in self._warned:
                self._warned.add(resource_id)
                _log.warning(
                    "catalog row %s is not a Tegel 0.3 row and must be re-ingested (%s)",
                    resource_id,
                    exc,
                )
            return None

    def keys(self) -> list[str]:
        with sqlite3.connect(sqlite_path(self.catalog_uri)) as connection:
            rows = connection.execute(
                "SELECT key FROM nodes WHERE id != 0 ORDER BY key"
            ).fetchall()
        return [row[0] for row in rows if isinstance(row[0], str) and row[0]]

    def _metadata(self, resource_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(sqlite_path(self.catalog_uri)) as connection:
            row = connection.execute(
                "SELECT metadata FROM nodes WHERE key = ? AND id != 0",
                (resource_id,),
            ).fetchone()
        if row is None:
            return None
        payload = row[0]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            return None
        return payload
