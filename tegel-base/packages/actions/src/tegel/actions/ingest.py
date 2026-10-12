"""Prefect flow that stages records, asks the updater to commit them, then refreshes."""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import httpx
from prefect import flow
from prefect.deployments import run_deployment

from tegel.actions.refresh import refresh_catalog
from tegel.actions.staging import claim_paths, write_staging

UPDATER = "update-catalog/catalog-updater"
INGEST = "ingest-directories/ingest-directories"


@flow(name="ingest-directories")
def ingest_directories(records: str = "", batch_size: int = 100) -> int:
    """Stage records in batches. Each batch is merged, then the catalog is reopened."""
    root = Path(records or _required("TEGEL_RECORDS"))
    if not root.is_dir():
        raise RuntimeError(f"records directory not found: {root}")
    if batch_size < 1:
        raise RuntimeError("batch_size must be at least 1")
    accepted = claim_paths(root)
    if not accepted:
        raise RuntimeError(f"no record in {root}")
    production = str(Path(_required("TEGEL_OUTPUT")) / "catalog.db")
    total = 0
    for batch in _chunks(accepted, batch_size):
        staging = Path(_required("TEGEL_OUTPUT")) / "staging" / f"{uuid.uuid4().hex}.db"
        count = write_staging(batch, staging)
        if count == 0:
            staging.unlink(missing_ok=True)
            continue
        run = run_deployment(
            name=UPDATER,
            parameters={"staging_path": str(staging), "production_path": production},
            as_subflow=False,
        )
        if run.state is None or not run.state.is_completed():
            raise RuntimeError(f"catalog update did not finish: {run.state}")
        refresh_catalog()
        total += count
        print(f"Committed {count} record(s)", flush=True)
    if total == 0:
        raise RuntimeError(f"no record in {root}")
    return total


def submit_directories(records: str) -> None:
    """Ask the serving ingest deployment to run. Retry until it is registered."""
    deadline = time.time() + 180
    last = ""
    while time.time() < deadline:
        try:
            run = run_deployment(
                name=INGEST,
                parameters={"records": records},
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


def main() -> None:
    """Serve the ingest deployment, or submit one directories run."""
    if len(sys.argv) > 1 and sys.argv[1] == "directories":
        records = sys.argv[2] if len(sys.argv) > 2 else _required("TEGEL_RECORDS")
        submit_directories(records)
        return
    _wait_for_api()
    ingest_directories.serve(name="ingest-directories")


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
