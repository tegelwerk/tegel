"""Write claimed records into a staging catalog. The production file is not opened."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from tegel.core.contracts.resources import resource_id_for
from tegel.core.contracts.types import SearchProfile
from tegel.core.plugins import PluginContractError, Registry, catalog_metadata
from tegel.core.spec import load_spec, record_slots, schema_root_from_env


def claim_paths(root: Path) -> list[str]:
    """Return files the first matching extractor accepts, in path order."""
    registry = Registry()
    accepted: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _hidden(path):
            continue
        try:
            registry.extractor_for(str(path))
        except PluginContractError:
            continue
        accepted.append(str(path))
    return accepted


def load_staging_spec() -> tuple[SearchProfile, frozenset[str]]:
    """The profile at ``TEGEL_PROFILE`` and its class slots, from ``TEGEL_SCHEMA_ROOT``."""
    value = os.environ.get("TEGEL_PROFILE", "").strip()
    if not value:
        raise RuntimeError("TEGEL_PROFILE is required")
    profile, schema = load_spec(Path(value), schema_root_from_env())
    return profile, record_slots(profile, schema)


def write_staging(paths: list[str], staging: Path) -> int:
    """Claim each path and write one ``{record, tegel}`` node into a new staging catalog.

    Record keys must be slots of the profile's class. The catalog id is the
    record's ``entity.identifier`` value.
    """
    profile, slots = load_staging_spec()
    registry = Registry()
    types = registry.resource_types()
    tree = _open(staging)
    count = 0
    for raw in paths:
        try:
            plugin = registry.extractor_for(raw)
        except PluginContractError:
            print(f"skipped {raw}", flush=True)
            continue
        package = plugin.process(raw, {})
        stored = catalog_metadata(
            plugin,
            package,
            source_path=raw,
            profile=profile,
            slots=slots,
            resource_types=types,
        )
        key = resource_id_for(package.record, profile)
        _write_node(tree, key=key, metadata=stored)
        count += 1
        print(f"staged {key}", flush=True)
    return count


def _open(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        candidate.unlink(missing_ok=True)
    from tiled.catalog import from_uri

    uri = f"sqlite:///{path.resolve()}"
    tree = from_uri(uri, init_if_not_exists=True)
    if not hasattr(tree.context, "streaming_cache"):
        tree.context.streaming_cache = None
    return tree


def _write_node(tree: Any, *, key: str, metadata: dict[str, Any]) -> None:
    from tiled.structures.core import StructureFamily

    if not hasattr(tree.context, "streaming_cache"):
        tree.context.streaming_cache = None

    async def _write() -> None:
        await tree.create_node(
            structure_family=StructureFamily.bytes,
            metadata=metadata,
            key=key,
        )

    asyncio.run(_write())


def _hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)
