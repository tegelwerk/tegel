"""Read-only view of a Tiled catalog."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from tegel.core.contracts.resources import Resource, resource_from_catalog, resource_uri
from tegel.core.contracts.types import SearchProfile
from tegel.dataserver.catalog import sqlite_path


class TiledCatalogIndex:
    """``get`` and ``keys`` over the catalog the ETL application committed.

    A no-argument constructor reads ``TEGEL_CATALOG_URI`` and
    ``TEGEL_PUBLIC_ORIGIN``. The launch process passes the open catalog's
    URI and the loaded profile directly.
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

    def get(self, resource_id: str) -> Resource | None:
        metadata = self._metadata(resource_id)
        if metadata is None:
            return None
        type_name = _text(metadata.get("resource_type"))
        type_version = _text(metadata.get("resource_type_version"))
        return resource_from_catalog(
            resource_id,
            resource_uri(self.public_origin, resource_id),
            metadata,
            type_name=type_name,
            type_version=type_version,
            profile=self.profile,
        )

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


def _text(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
