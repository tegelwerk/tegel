"""Merge a staging catalog into the production catalog.

The catalog updater is the only writer of the production file. Closure and
full-text rows are left to the triggers on ``nodes``. ``alembic_version`` is
not copied.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

_SKIP = {"alembic_version", "nodes_closure", "metadata_fts5"}


def merge_staging(staging_path: str, production_path: str) -> str:
    """Copy staging nodes into production and remove the staging file."""
    if not os.path.exists(staging_path):
        raise RuntimeError(f"staging catalog not found: {staging_path}")
    if not os.path.exists(production_path):
        Path(production_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(staging_path, production_path)
        os.remove(staging_path)
        return production_path

    connection = sqlite3.connect(production_path)
    connection.row_factory = sqlite3.Row
    attached = False
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("ATTACH DATABASE ? AS staging_db", (staging_path,))
        attached = True
        connection.execute("BEGIN")
        _merge(connection)
        connection.commit()
    except Exception as exc:
        connection.rollback()
        raise RuntimeError(f"catalog merge failed: {exc}") from exc
    finally:
        if attached:
            connection.execute("DETACH DATABASE staging_db")
        connection.close()
    os.remove(staging_path)
    return production_path


def _merge(connection: sqlite3.Connection) -> None:
    _require(connection, "staging_db", "nodes")
    _require(connection, "main", "nodes")
    staging_root = connection.execute(
        "SELECT id FROM staging_db.nodes WHERE parent IS NULL"
    ).fetchone()
    production_root = connection.execute(
        "SELECT id FROM main.nodes WHERE parent IS NULL"
    ).fetchone()
    if staging_root is None or production_root is None:
        raise RuntimeError("catalog is missing its root node")
    id_map = {int(staging_root["id"]): int(production_root["id"])}
    _walk(connection, int(staging_root["id"]), id_map)


def _walk(connection: sqlite3.Connection, staging_parent: int, id_map: dict[int, int]) -> None:
    rows = connection.execute(
        "SELECT * FROM staging_db.nodes WHERE parent = ? ORDER BY id",
        (staging_parent,),
    ).fetchall()
    for row in rows:
        staging_id = int(row["id"])
        production_parent = id_map[int(row["parent"])]
        existing = connection.execute(
            "SELECT id FROM main.nodes WHERE parent = ? AND key = ?",
            (production_parent, row["key"]),
        ).fetchone()
        if existing is None:
            production_id = _insert_node(connection, row, production_parent)
            _copy_revisions(connection, staging_id, production_id)
        else:
            production_id = int(existing["id"])
            _update_node(connection, row, production_id)
            _delete_sources(connection, production_id)
        id_map[staging_id] = production_id
        _copy_payload(connection, staging_id, production_id)
        _walk(connection, staging_id, id_map)


def _insert_node(connection: sqlite3.Connection, row: sqlite3.Row, parent_id: int) -> int:
    columns = [
        name
        for name in _columns(connection, "main", "nodes")
        if name != "id" and name in row.keys()
    ]
    values = [parent_id if name == "parent" else row[name] for name in columns]
    placeholders = ", ".join("?" for _ in columns)
    listed = ", ".join(columns)
    cursor = connection.execute(
        f"INSERT INTO main.nodes ({listed}) VALUES ({placeholders})",
        values,
    )
    return int(cursor.lastrowid)


def _update_node(connection: sqlite3.Connection, row: sqlite3.Row, production_id: int) -> None:
    assignments: list[str] = []
    values: list[object] = []
    for name in ("metadata", "specs", "structure_family", "time_updated"):
        if name in row.keys():
            assignments.append(f"{name} = ?")
            values.append(row[name])
    if not assignments:
        return
    values.append(production_id)
    connection.execute(
        f"UPDATE main.nodes SET {', '.join(assignments)} WHERE id = ?",
        values,
    )


def _delete_sources(connection: sqlite3.Connection, node_id: int) -> None:
    if not _has(connection, "main", "data_sources"):
        return
    if _has(connection, "main", "data_source_asset_association"):
        connection.execute(
            """
            DELETE FROM main.data_source_asset_association
            WHERE data_source_id IN (
                SELECT id FROM main.data_sources WHERE node_id = ?
            )
            """,
            (node_id,),
        )
    connection.execute("DELETE FROM main.data_sources WHERE node_id = ?", (node_id,))


def _copy_payload(connection: sqlite3.Connection, staging_id: int, production_id: int) -> None:
    if not _has(connection, "staging_db", "data_sources"):
        return
    sources = connection.execute(
        "SELECT * FROM staging_db.data_sources WHERE node_id = ? ORDER BY id",
        (staging_id,),
    ).fetchall()
    for source in sources:
        structure_id = source["structure_id"] if "structure_id" in source.keys() else None
        if structure_id is not None:
            _copy_structure(connection, structure_id)
        assets = _copy_assets(connection, int(source["id"]))
        production_source = _insert_row(
            connection,
            "data_sources",
            source,
            overrides={"node_id": production_id},
        )
        _copy_links(connection, int(source["id"]), production_source, assets)


def _copy_structure(connection: sqlite3.Connection, structure_id: object) -> None:
    if not _has(connection, "staging_db", "structures"):
        return
    row = connection.execute(
        "SELECT * FROM staging_db.structures WHERE id = ?",
        (structure_id,),
    ).fetchone()
    if row is None:
        return
    columns = [name for name in row.keys() if name in _columns(connection, "main", "structures")]
    placeholders = ", ".join("?" for _ in columns)
    listed = ", ".join(columns)
    connection.execute(
        f"INSERT OR IGNORE INTO main.structures ({listed}) VALUES ({placeholders})",
        [row[name] for name in columns],
    )


def _copy_assets(connection: sqlite3.Connection, staging_source: int) -> dict[int, int]:
    found: dict[int, int] = {}
    if not _has(connection, "staging_db", "data_source_asset_association"):
        return found
    links = connection.execute(
        """
        SELECT asset_id FROM staging_db.data_source_asset_association
        WHERE data_source_id = ?
        """,
        (staging_source,),
    ).fetchall()
    for link in links:
        staging_asset = int(link["asset_id"])
        if staging_asset in found:
            continue
        asset = connection.execute(
            "SELECT * FROM staging_db.assets WHERE id = ?",
            (staging_asset,),
        ).fetchone()
        if asset is None:
            continue
        existing = connection.execute(
            "SELECT id FROM main.assets WHERE data_uri = ?",
            (asset["data_uri"],),
        ).fetchone()
        if existing is not None:
            found[staging_asset] = int(existing["id"])
            continue
        found[staging_asset] = _insert_row(connection, "assets", asset, overrides={})
    return found


def _copy_links(
    connection: sqlite3.Connection,
    staging_source: int,
    production_source: int,
    assets: dict[int, int],
) -> None:
    if not _has(connection, "staging_db", "data_source_asset_association"):
        return
    rows = connection.execute(
        """
        SELECT * FROM staging_db.data_source_asset_association
        WHERE data_source_id = ?
        """,
        (staging_source,),
    ).fetchall()
    for row in rows:
        staging_asset = int(row["asset_id"])
        _insert_row(
            connection,
            "data_source_asset_association",
            row,
            overrides={
                "data_source_id": production_source,
                "asset_id": assets[staging_asset],
            },
            keep_id=False,
        )


def _copy_revisions(connection: sqlite3.Connection, staging_id: int, production_id: int) -> None:
    if not _has(connection, "staging_db", "revisions"):
        return
    rows = connection.execute(
        "SELECT * FROM staging_db.revisions WHERE node_id = ? ORDER BY id",
        (staging_id,),
    ).fetchall()
    for row in rows:
        _insert_row(connection, "revisions", row, overrides={"node_id": production_id})


def _insert_row(
    connection: sqlite3.Connection,
    table: str,
    row: sqlite3.Row,
    *,
    overrides: dict[str, object],
    keep_id: bool = False,
) -> int:
    main_columns = _columns(connection, "main", table)
    columns: list[str] = []
    values: list[object] = []
    for name in row.keys():
        if name not in main_columns:
            continue
        if name == "id" and not keep_id:
            continue
        columns.append(name)
        values.append(overrides[name] if name in overrides else row[name])
    placeholders = ", ".join("?" for _ in columns)
    listed = ", ".join(columns)
    cursor = connection.execute(
        f"INSERT INTO main.{table} ({listed}) VALUES ({placeholders})",
        values,
    )
    return int(cursor.lastrowid)


def _columns(connection: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA {schema}.table_info({table})")]


def _has(connection: sqlite3.Connection, schema: str, table: str) -> bool:
    if table in _SKIP:
        return False
    row = connection.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _require(connection: sqlite3.Connection, schema: str, table: str) -> None:
    if not _has(connection, schema, table):
        raise RuntimeError(f"{schema}.{table} is missing")
