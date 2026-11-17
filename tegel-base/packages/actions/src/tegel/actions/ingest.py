"""Prefect flows that stage records, ask the updater to commit them, then refresh.

- ``ingest-directories``: every record under ``TEGEL_RECORDS``.
- ``ingest-raw``: pre-ingest. The installed converters (``tegel.converters``) turn
  ready raw files under ``TEGEL_RAW`` into records under ``TEGEL_RECORDS``; exactly
  those records are then staged and committed. Served on an interval
  (``python -m tegel.actions.ingest raw --interval 60``) it watches the raw directory.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from prefect import flow, task
from prefect.artifacts import create_markdown_artifact, create_table_artifact
from prefect.deployments import run_deployment

from tegel.actions import preingest
from tegel.actions.errorlog import FILENAME as ERROR_LOG
from tegel.actions.parts import readable_roots
from tegel.actions.refresh import refresh_catalog
from tegel.actions.staging import (
    MODES,
    StagingError,
    claim_paths,
    remove_staging,
    write_staging,
)
from tegel.core.plugins import Registry

UPDATER = "update-catalog/catalog-updater"
INGEST = "ingest-directories/ingest-directories"
INGEST_RAW = "ingest-raw/ingest-raw"


def resolve_on_error(value: str = "") -> str:
    """The error mode: ``value``, else ``TEGEL_ON_ERROR``, else ``skip``."""
    chosen = (value or os.environ.get("TEGEL_ON_ERROR", "")).strip() or "skip"
    if chosen not in MODES:
        raise RuntimeError(f"TEGEL_ON_ERROR must be skip or fail: got {chosen!r}")
    return chosen


@flow(name="ingest-directories")
def ingest_directories(records: str = "", batch_size: int = 100, on_error: str = "") -> int:
    """Stage records in batches. Each batch is merged, then the catalog is reopened."""
    mode = resolve_on_error(on_error)
    root = Path(records or _required("TEGEL_RECORDS"))
    if not root.is_dir():
        raise RuntimeError(f"records directory not found: {root}")
    if batch_size < 1:
        raise RuntimeError("batch_size must be at least 1")
    accepted = claim_paths(root)
    if not accepted:
        raise RuntimeError(f"no record in {root}")
    output = Path(_required("TEGEL_OUTPUT"))
    error_log = output / ERROR_LOG
    roots = readable_roots() or [root.resolve()]
    total, skipped = commit(accepted, output, mode=mode, run_id=_run_id(), roots=roots,
                            batch_size=batch_size)
    if total == 0:
        raise RuntimeError(
            f"no record staged from {root}: {skipped} skipped; errors in {error_log}"
        )
    return total


def commit(paths: list[str], output: Path, *, mode: str, run_id: str, roots: list[Path],
           batch_size: int = 100) -> tuple[int, int]:
    """Stage ``paths`` in batches; merge each through the updater, then refresh.

    Returns (staged, skipped).
    """
    production = str(output / "catalog.db")
    error_log = output / ERROR_LOG
    total = 0
    skipped = 0
    try:
        for batch in _chunks(paths, batch_size):
            staging = output / "staging" / f"{uuid.uuid4().hex}.db"
            result = write_staging(
                batch, staging, on_error=mode, error_log=error_log, run_id=run_id, roots=roots
            )
            skipped += result.skipped
            if result.staged == 0:
                remove_staging(staging)
                continue
            run = run_deployment(
                name=UPDATER,
                parameters={"staging_path": str(staging), "production_path": production},
                as_subflow=False,
            )
            if run.state is None or not run.state.is_completed():
                raise RuntimeError(f"catalog update did not finish: {run.state}")
            refresh_catalog()
            total += result.staged
            print(f"Committed {result.staged} record(s)", flush=True)
    except StagingError:
        skipped += 1
        raise
    finally:
        print(summary(total, skipped, error_log), flush=True)
    return total, skipped


@task(name="find-ready-raw-files")
def _find_ready(roots: list[str], stable: float) -> list[dict]:
    converters = Registry().converters
    ledger = preingest.ledger_for(Path(_required("TEGEL_OUTPUT")))
    units = preingest.pending([Path(r) for r in roots], converters, ledger, stable=stable)
    ledger.save()
    print(f"{len(units)} unit(s) ready under {', '.join(roots)}", flush=True)
    return [{"converter": u.converter, "collection": u.collection,
             "files": [str(f) for f in u.files]} for u in units]


@task(name="convert", retries=1, retry_delay_seconds=10,
      task_run_name="convert {unit[files][0]}")
def _convert(unit: dict, records: str, config: dict) -> list[str]:
    converter = Registry().converters[unit["converter"]]
    found = preingest.Unit(unit["converter"], unit["collection"],
                           [Path(f) for f in unit["files"]])
    written = preingest.convert_unit(converter, found, Path(records), config)
    print(f"{unit['converter']}: {', '.join(Path(f).name for f in unit['files'])} -> "
          f"{', '.join(str(Path(r).parent.name) for r in written)}", flush=True)
    return [str(r) for r in written]


@flow(name="ingest-raw")
def ingest_raw(raw: str = "", records: str = "", stable: float = -1.0,
               on_error: str = "", batch_size: int = 100) -> int:
    """Pre-ingest: convert ready raw files with the installed converters, then ingest
    exactly the records they wrote. An idle pass (nothing ready) returns 0."""
    mode = resolve_on_error(on_error)
    roots = preingest.raw_roots(raw)
    if not roots:
        raise RuntimeError("no raw directory: pass raw or set TEGEL_RAW")
    root = Path(records or _required("TEGEL_RECORDS"))
    output = Path(_required("TEGEL_OUTPUT"))
    if stable < 0:
        stable = float(os.environ.get("TEGEL_RAW_STABLE", "") or 60)
    config = preingest.converter_config()
    ledger = preingest.ledger_for(output)
    result = preingest.PreIngestResult()
    for unit in _find_ready([str(r) for r in roots], stable):
        found = preingest.Unit(unit["converter"], unit["collection"],
                               [Path(f) for f in unit["files"]])
        try:
            written = [Path(r) for r in _convert(unit, str(root), config)]
        except Exception as exc:
            preingest.record_outcome(ledger, result, found, None, exc)
        else:
            preingest.record_outcome(ledger, result, found, written, None)
    if not result.converted and not result.failed:
        return 0
    total = 0
    skipped = 0
    if result.records:
        roots_readable = readable_roots() or [root.resolve()]
        total, skipped = commit([str(r) for r in result.records], output, mode=mode,
                                run_id=_run_id(), roots=roots_readable,
                                batch_size=batch_size)
    _report_raw(result, total, skipped, output / ERROR_LOG)
    return total


def _report_raw(result: preingest.PreIngestResult, staged: int, skipped: int,
                error_log: Path) -> None:
    rows = [{"converter": item["converter"], "collection": item["collection"] or "",
             "raw files": "; ".join(Path(f).name for f in item["files"]),
             "records": "; ".join(str(Path(r).parent) for r in item["records"])}
            for item in result.converted]
    if rows:
        create_table_artifact(key="ingest-raw-converted", table=rows,
                              description="Raw files converted and the records written")
    lines = [f"**Converted units:** {len(result.converted)}  ",
             f"**Records staged:** {staged}, skipped {skipped}  ",
             f"**Failed units:** {len(result.failed)}"]
    lines += [f"- {', '.join(Path(f).name for f in item['files'])}: {item['error']}"
              for item in result.failed]
    if skipped:
        lines.append(f"Staging errors are in `{error_log}`.")
    create_markdown_artifact(key="ingest-raw-summary", markdown="\n".join(lines),
                             description="Pre-ingest and ingest summary")


def summary(staged: int, skipped: int, error_log: Path) -> str:
    """One line per run. The log path is named only when a record was skipped."""
    line = f"staged {staged}, skipped {skipped}"
    if skipped > 0:
        line += f"; errors in {error_log}"
    return line


def submit_directories(records: str, on_error: str = "") -> None:
    """Ask the serving ingest deployment to run. Retry until it is registered."""
    parameters = {"records": records}
    if on_error or os.environ.get("TEGEL_ON_ERROR", "").strip():
        parameters["on_error"] = resolve_on_error(on_error)
    deadline = time.time() + 180
    last = ""
    while time.time() < deadline:
        try:
            run = run_deployment(
                name=INGEST,
                parameters=parameters,
                as_subflow=False,
            )
        except Exception as exc:
            last = str(exc)
            print(f"Ingest deployment not ready: {last}", flush=True)
            time.sleep(2)
            continue
        if run.state is None or not run.state.is_completed():
            raise SystemExit(f"ingest did not finish: {run.state}")
        print("Ingest finished", flush=True)
        return
    raise SystemExit(f"ingest deployment was not ready: {last}")


def main(argv: list[str] | None = None) -> None:
    """Serve the ingest deployment, or submit one directories run."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "directories":
        parser = argparse.ArgumentParser(prog="python -m tegel.actions.ingest directories")
        parser.add_argument("records", nargs="?", default="")
        parser.add_argument("--on-error", choices=MODES, default="")
        options = parser.parse_args(args[1:])
        submit_directories(options.records or _required("TEGEL_RECORDS"), options.on_error)
        return
    if args and args[0] == "raw":
        parser = argparse.ArgumentParser(prog="python -m tegel.actions.ingest raw")
        parser.add_argument("--interval", type=float, default=0.0,
                            help="seconds between scheduled passes (0: serve without schedule)")
        options = parser.parse_args(args[1:])
        _wait_for_api()
        from prefect import serve

        raw = ingest_raw.to_deployment(
            name="ingest-raw", interval=options.interval or None,
            description="Convert ready raw files (TEGEL_RAW) and ingest the new records",
        )
        directories = ingest_directories.to_deployment(name="ingest-directories")
        serve(raw, directories, limit=1)
        return
    _wait_for_api()
    ingest_directories.serve(name="ingest-directories")


def _run_id() -> str:
    from prefect.runtime import flow_run

    return str(flow_run.id or uuid.uuid4().hex)


def _wait_for_api() -> None:
    base = os.environ.get("PREFECT_API_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("PREFECT_API_URL is required")
    url = base + "/health"
    deadline = time.time() + 180
    last = ""
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
        except httpx.HTTPError as exc:
            last = str(exc)
            time.sleep(1)
            continue
        if response.status_code < 500:
            return
        last = str(response.status_code)
        time.sleep(1)
    raise RuntimeError(f"Prefect API was not ready: {last}")


def _chunks(paths: list[str], batch_size: int) -> list[list[str]]:
    return [paths[start : start + batch_size] for start in range(0, len(paths), batch_size)]


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(exc, file=sys.stderr)
        raise
