"""Prefect flow that stages records, asks the updater to commit them, then refreshes."""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from prefect import flow
from prefect.deployments import run_deployment

from tegel.actions.errorlog import FILENAME as ERROR_LOG
from tegel.actions.refresh import refresh_catalog
from tegel.actions.staging import (
    MODES,
    StagingError,
    claim_paths,
    remove_staging,
    write_staging,
)

UPDATER = "update-catalog/catalog-updater"
INGEST = "ingest-directories/ingest-directories"


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
    production = str(output / "catalog.db")
    error_log = output / ERROR_LOG
    run_id = _run_id()
    total = 0
    skipped = 0
    try:
        for batch in _chunks(accepted, batch_size):
            staging = output / "staging" / f"{uuid.uuid4().hex}.db"
            result = write_staging(
                batch, staging, on_error=mode, error_log=error_log, run_id=run_id
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
    if total == 0:
        raise RuntimeError(
            f"no record staged from {root}: {skipped} skipped; errors in {error_log}"
        )
    return total


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
