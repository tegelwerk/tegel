"""Write claimed records into a staging catalog. The production file is not opened."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from tegel.core.plugins import PluginContractError, Registry, check_package


def catalog_key(metadata: dict[str, Any]) -> str:
    """Use an explicit key, then uuid. The value must be one path segment."""
    for name in ("key", "uuid"):
        value = metadata.get(name)
        if isinstance(value, str) and value.strip():
            key = value.strip()
            if "/" in key or "\\" in key:
                raise ValueError(f"catalog key must be one path segment: {key}")
            return key
    raise ValueError("catalog key is missing")


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


def write_staging(paths: list[str], staging: Path) -> int:
    """Claim each path and write one node into a new staging catalog."""
    registry = Registry()
    tree = _open(staging)
    count = 0
    for raw in paths:
        try:
            plugin = registry.extractor_for(raw)
        except PluginContractError:
            print(f"skipped {raw}", flush=True)
            continue
        package = check_package(plugin, plugin.process(raw, {}))
        key = catalog_key(package.metadata)
        _write_node(tree, key=key, metadata=package.metadata)
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
