"""Write claimed records into a staging catalog. The production file is not opened."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tegel.actions.errorlog import ErrorLog
from tegel.actions.parts import (
    PartError,
    RemoteChecker,
    plan_parts,
    readable_roots,
    write_resource,
)
from tegel.core.contracts.resources import Resource, resource_from_catalog, resource_id_for
from tegel.core.contracts.types import SearchProfile
from tegel.core.plugins import (
    PluginContractError,
    Registry,
    catalog_metadata,
    describe_parts,
    readers_in_order,
    schema_mismatch,
    with_parts,
)
from tegel.core.spec import load_spec, record_slots, schema_root_from_env
from tegel.core.upstreams import Upstream, load_upstreams, upstream_timeout

LOGGER = logging.getLogger("tegel.staging")
MODES = ("skip", "fail")


class StagingError(Exception):
    """One bad record: the path, the stage that failed, and why."""

    def __init__(self, path: str, stage: str, error: str, message: str) -> None:
        super().__init__(f"{path}: {stage}: {message}")
        self.path = path
        self.stage = stage
        self.error = error
        self.message = message


@dataclass
class StagingResult:
    staged: int = 0
    skipped: int = 0
    errors: list[StagingError] = field(default_factory=list)


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


def write_staging(
    paths: list[str],
    staging: Path,
    *,
    on_error: str = "skip",
    error_log: Path | None = None,
    run_id: str | None = None,
    roots: list[Path] | None = None,
    upstreams: dict[str, Upstream] | None = None,
    upstream_client: httpx.Client | None = None,
) -> StagingResult:
    """Claim each path and write one ``{record, tegel}`` node into a new staging catalog.

    A bad record is logged. With ``on_error="skip"`` staging continues; with
    ``"fail"`` the staging catalog is deleted and ``StagingError`` is raised.
    A profile, schema, or resource-type problem always raises.

    Parts are described once here and stored in the envelope. Tiled parts must
    lie under ``roots``, which default to ``readable_roots()``. Remote parts must
    name an upstream in ``upstreams``, which default to ``TEGEL_UPSTREAMS``.
    """
    if on_error not in MODES:
        raise ValueError(f"on_error must be skip or fail: got {on_error!r}")
    profile, slots = load_staging_spec()
    registry = Registry()
    types = registry.resource_types()
    problem = schema_mismatch(types, profile)
    if problem is not None:
        raise RuntimeError(problem)
    readers = readers_in_order(registry)
    roots = readable_roots() if roots is None else roots
    upstreams = load_upstreams() if upstreams is None else upstreams
    owned = upstream_client is None
    http = upstream_client or httpx.Client(timeout=upstream_timeout(), follow_redirects=False)
    check_remote = RemoteChecker(upstreams, http)
    log = ErrorLog(error_log, run_id=run_id or uuid.uuid4().hex, mode=on_error)
    tree = _open(staging, roots)
    result = StagingResult()
    seen: set[str] = set()

    def bad(
        stage: str,
        raw: str,
        exc: Exception,
        plugin_id: str | None,
        resource_id: str | None = None,
    ) -> None:
        line = log.record(
            stage=stage, path=raw, exc=exc, plugin_id=plugin_id, resource_id=resource_id
        )
        error = StagingError(raw, stage, str(line["error"]), str(line["message"]))
        result.skipped += 1
        result.errors.append(error)
        if on_error == "fail":
            _discard(tree, staging)
            raise error from exc

    try:
        for raw in paths:
            try:
                plugin = registry.extractor_for(raw)
            except PluginContractError:
                LOGGER.info("no extractor claims %s", raw)
                continue
            try:
                package = plugin.process(raw, {})
            except Exception as exc:
                bad("process", raw, exc, plugin.plugin_id)
                continue
            try:
                stored = catalog_metadata(
                    plugin,
                    package,
                    source_path=raw,
                    profile=profile,
                    slots=slots,
                    resource_types=types,
                )
            except (PluginContractError, ValueError) as exc:
                bad("check", raw, exc, plugin.plugin_id)
                continue
            try:
                resource = _provisional_resource(stored, package.record, profile, raw)
                descriptor = describe_parts(readers, resource)
                plan = plan_parts(
                    descriptor,
                    payload_uri=resource.payload_uri,
                    roots=roots,
                    check_remote=check_remote,
                )
                stored = with_parts(stored, plan.parts, plan.payload)
            except (PluginContractError, ValueError) as exc:
                bad("parts", raw, exc, plugin.plugin_id)
                continue
            try:
                key = resource_id_for(package.record, profile)
            except ValueError as exc:
                bad("id", raw, exc, plugin.plugin_id)
                continue
            if key in seen:
                duplicate = ValueError(f"resource id {key} is already staged in this batch")
                bad("duplicate", raw, duplicate, plugin.plugin_id, key)
                continue
            try:
                write_resource(tree, key=key, metadata=stored, plan=plan)
            except PartError as exc:
                bad("parts", raw, exc, plugin.plugin_id, key)
                continue
            except Exception as exc:
                bad("write", raw, exc, plugin.plugin_id, key)
                continue
            seen.add(key)
            result.staged += 1
            LOGGER.info("staged %s", key)
    except StagingError as exc:
        if exc not in result.errors:
            _discard(tree, staging)
        raise
    except BaseException:
        _discard(tree, staging)
        raise
    finally:
        if owned:
            http.close()
    _close(tree)
    return result


def _provisional_resource(
    stored: dict[str, Any], record: dict[str, Any], profile: SearchProfile, raw: str
) -> Resource:
    value = record.get(profile.entity.identifier)
    provisional = str(value) if value not in (None, "") else Path(raw).stem
    return resource_from_catalog(
        provisional, f"urn:tegel:staging/{provisional}", stored, profile=profile
    )


def _open(path: Path, roots: list[Path]) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        candidate.unlink(missing_ok=True)
    from tiled.catalog import from_uri

    uri = f"sqlite:///{path.resolve()}"
    tree = from_uri(
        uri, init_if_not_exists=True, readable_storage=[str(root) for root in roots]
    )
    if not hasattr(tree.context, "streaming_cache"):
        tree.context.streaming_cache = None
    return tree


def remove_staging(path: Path) -> None:
    """Remove a staging catalog and its sqlite ``-wal`` and ``-shm`` files."""
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)


def _close(tree: Any) -> None:
    engine = getattr(tree.context, "engine", None)
    if engine is not None:
        asyncio.run(engine.dispose())


def _discard(tree: Any, path: Path) -> None:
    _close(tree)
    remove_staging(path)


def _hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)
