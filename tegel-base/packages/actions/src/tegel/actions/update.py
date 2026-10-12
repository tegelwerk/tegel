"""Prefect flow that commits one staging catalog. It does not talk to the data server."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
from prefect import flow

from tegel.actions.merge import merge_staging


@flow(name="update-catalog")
def update_catalog(staging_path: str, production_path: str = "") -> str:
    """Merge a staging catalog into the production catalog."""
    production = production_path or _production()
    print(f"Merging {staging_path} into {production}", flush=True)
    return merge_staging(staging_path, production)


def _production() -> str:
    output = os.environ.get("TEGEL_OUTPUT", "").strip()
    if not output:
        raise RuntimeError("TEGEL_OUTPUT is required")
    return str(Path(output) / "catalog.db")


def main() -> None:
    """Serve the catalog-updater deployment. One run at a time."""
    _wait_for_api()
    update_catalog.serve(name="catalog-updater", limit=1)


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


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(exc, file=sys.stderr)
        raise
