"""Index lists top-level resources only; merge drops part children that staging lost."""

import sqlite3
from pathlib import Path

import pytest
from tiled.utils import ensure_uri

from tegel.actions import merge as merge_module
from tegel.actions.merge import merge_staging
from tegel.actions.staging import write_staging
from tegel.core.contracts.resources import DataDescriptor
from tegel.dataserver.index.tiled_catalog import TiledCatalogIndex
from tegel.toy.dataserver import ToyReader
from tegel.toy.dataserver.fixtures import write_record

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PROFILE = EXAMPLES / "toy" / "schema" / "profile.yaml"
RECORD = {"uuid": "rec-a", "sample_name": "a", "channel": "bin", "calibrated": True}


@pytest.fixture(autouse=True)
def profile(monkeypatch) -> None:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    monkeypatch.setenv("TEGEL_SCHEMA_ROOT", str(EXAMPLES / "schemas"))


def _ingest(tmp_path: Path, markers: list[Path], name: str) -> Path:
    staging = tmp_path / f"{name}.db"
    result = write_staging([str(marker) for marker in markers], staging, roots=[tmp_path])
    assert result.skipped == 0, result.errors
    production = tmp_path / "production.db"
    merge_staging(str(staging), str(production))
    return production


def _children(production: Path, key: str) -> list[str]:
    with sqlite3.connect(production) as connection:
        rows = connection.execute(
            """
            SELECT child.key FROM nodes AS child JOIN nodes AS parent ON child.parent = parent.id
            WHERE parent.key = ? ORDER BY child.key
            """,
            (key,),
        ).fetchall()
    return [row[0] for row in rows]


def _asset_uris(production: Path) -> set[str]:
    with sqlite3.connect(production) as connection:
        return {row[0] for row in connection.execute("SELECT data_uri FROM assets")}


def _dump(production: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(production) as connection:
        return {
            table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
            for table in ("nodes", "data_sources", "assets", "structures", "nodes_closure")
        }


def test_keys_exclude_part_children(tmp_path: Path) -> None:
    marker = write_record(tmp_path / "a", RECORD)
    production = _ingest(tmp_path, [marker], "first")
    assert _children(production, "rec-a") == ["frames", "stats"]
    index = TiledCatalogIndex(f"sqlite:///{production}", "http://test")
    assert index.keys() == ["rec-a"]
    assert index.get("frames") is None
    assert index.get("rec-a") is not None


def test_reingest_drops_removed_part(tmp_path: Path) -> None:
    marker = write_record(tmp_path / "a", RECORD)
    production = _ingest(tmp_path, [marker], "first")
    stats_uri = ensure_uri(str(tmp_path / "a" / "stats.csv"))
    assert stats_uri in _asset_uris(production)
    (tmp_path / "a" / "stats.csv").unlink()
    _ingest(tmp_path, [marker], "second")
    assert _children(production, "rec-a") == ["frames"]
    assert stats_uri not in _asset_uris(production)
    with sqlite3.connect(production) as connection:
        orphans = connection.execute(
            "SELECT COUNT(*) FROM structures WHERE id NOT IN "
            "(SELECT structure_id FROM data_sources WHERE structure_id IS NOT NULL)"
        ).fetchone()[0]
        closure = connection.execute(
            "SELECT COUNT(*) FROM nodes_closure WHERE descendant NOT IN (SELECT id FROM nodes)"
        ).fetchone()[0]
    assert (orphans, closure) == (0, 0)


def test_reingest_keeps_shared_asset(tmp_path: Path, monkeypatch) -> None:
    first = write_record(tmp_path / "a", RECORD, stats=False, notes=False)
    second = write_record(
        tmp_path / "b", {**RECORD, "uuid": "rec-b"}, frames=False, stats=False, notes=False
    )
    shared = ensure_uri(str(tmp_path / "a" / "frames.zarr"))
    original = ToyReader.describe
    state = {"share": True}

    def describe(self, resource):
        found = original(self, resource)
        if resource.id != "rec-b" or not state["share"]:
            return found
        borrowed = resource.model_copy(update={"payload_uri": first.as_uri()})
        [frames] = original(self, borrowed).parts
        return DataDescriptor(parts=[frames], payload=found.payload)

    monkeypatch.setattr(ToyReader, "describe", describe)
    production = _ingest(tmp_path, [first, second], "first")
    assert _children(production, "rec-b") == ["frames"]
    state["share"] = False
    _ingest(tmp_path, [second], "second")
    assert _children(production, "rec-b") == []
    assert _children(production, "rec-a") == ["frames"]
    assert shared in _asset_uris(production)


def test_merge_rollback_on_error(tmp_path: Path, monkeypatch) -> None:
    marker = write_record(tmp_path / "a", RECORD)
    production = _ingest(tmp_path, [marker], "first")
    before = _dump(production)
    (tmp_path / "a" / "stats.csv").unlink()
    staging = tmp_path / "second.db"
    write_staging([str(marker)], staging, roots=[tmp_path])

    def broken(connection, released) -> None:
        raise sqlite3.OperationalError("disk went away")

    monkeypatch.setattr(merge_module, "_drop_unused", broken)
    with pytest.raises(RuntimeError, match="catalog merge failed: disk went away"):
        merge_staging(str(staging), str(production))
    assert _dump(production) == before
    assert staging.exists()
