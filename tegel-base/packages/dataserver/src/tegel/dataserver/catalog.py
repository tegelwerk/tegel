"""Open a Tiled catalog that contains the entity/link graph.

The HTTP process reads this catalog. It does not walk a directory, claim a
plugin, or copy a payload. ``insert_record`` is the write a downstream ETL
application can call. No route calls it.
"""

from __future__ import annotations

import asyncio
import sqlite3
from importlib.metadata import version
from typing import Any

GRAPH_FLOOR = (0, 2, 15)


class CatalogError(RuntimeError):
    """The installed Tiled release cannot be the reference catalog."""


def require_tiled_graph() -> str:
    """Refuse to start when this Tiled install has no entity/link graph."""
    try:
        import tiled.graph  # noqa: F401
    except ImportError as exc:
        raise CatalogError("tiled.graph is not installed") from exc
    found = version("tiled")
    if _numbers(found) < GRAPH_FLOOR:
        raise CatalogError(
            f"tiled {found} has no entity/link graph; 0.2.15 or later is required"
        )
    return found


def open_catalog(uri: str) -> Any:
    """Initialize the catalog if needed and return the Tiled container."""
    require_tiled_graph()
    from tiled.catalog import from_uri

    tree = from_uri(uri, init_if_not_exists=True)
    if not catalog_has_graph(uri):
        raise CatalogError("The catalog database has no entities table")
    return tree


def catalog_has_graph(uri: str) -> bool:
    """True when the sqlite catalog contains the ``entities`` table."""
    with sqlite3.connect(sqlite_path(uri)) as connection:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entities'"
        ).fetchone()
    return row is not None


def sqlite_path(uri: str) -> str:
    """Return the file path inside a sqlite catalog URI."""
    if not uri.startswith("sqlite:"):
        raise CatalogError("The reference catalog URI must be sqlite")
    rest = uri.removeprefix("sqlite:")
    if "+aiosqlite:" in uri:
        rest = uri.split("+aiosqlite:", 1)[1]
    if rest.startswith("////"):
        return rest[3:]
    if rest.startswith("///"):
        return rest[3:]
    raise CatalogError("The catalog URI must be a sqlite file path")


def insert_record(
    tree: Any,
    *,
    key: str,
    metadata: dict[str, Any],
    structure_family: str,
) -> None:
    """Write one catalog node. ETL calls this. The read API does not."""
    from tiled.structures.core import StructureFamily

    family = StructureFamily(structure_family)
    if not hasattr(tree.context, "streaming_cache"):
        tree.context.streaming_cache = None

    async def _write() -> None:
        await tree.create_node(
            structure_family=family,
            metadata=metadata,
            key=key,
        )

    asyncio.run(_write())


def _numbers(text: str) -> tuple[int, ...]:
    core = text.split("+", 1)[0]
    for marker in ("rc", "a", "b", ".dev"):
        core = core.split(marker, 1)[0]
    parts = [int(item) for item in core.split(".") if item.isdigit()]
    return tuple(parts[:3])
