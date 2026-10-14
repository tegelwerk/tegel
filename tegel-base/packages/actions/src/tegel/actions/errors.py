"""Read the ingest error log: ``python -m tegel.actions.errors``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from tegel.actions.errorlog import FILENAME

STAGES = ("process", "check", "id", "duplicate", "write")


def read_lines(path: Path) -> list[tuple[str, dict]]:
    """Return ``(raw line, parsed line)`` pairs. A missing file has no lines."""
    if not path.is_file():
        return []
    found: list[tuple[str, dict]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            found.append((raw, parsed))
    return found


def select(
    lines: list[tuple[str, dict]],
    *,
    run: str | None = None,
    last: bool = False,
    stage: str | None = None,
) -> list[tuple[str, dict]]:
    """Filter by run id, the most recent run id, and stage."""
    if last and lines:
        run = lines[-1][1].get("run_id")
    return [
        (raw, line)
        for raw, line in lines
        if (run is None or line.get("run_id") == run)
        and (stage is None or line.get("stage") == stage)
    ]


def table(lines: list[dict]) -> str:
    """Plain columns: time, stage, path, message."""
    rows = [("time", "stage", "path", "message")]
    rows += [
        (
            str(line.get("time", "")),
            str(line.get("stage", "")),
            str(line.get("path", "")),
            str(line.get("message", "")),
        )
        for line in lines
    ]
    widths = [max(len(row[index]) for row in rows) for index in range(3)]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row[:3], widths)) + "  " + row[3]
        for row in rows
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tegel.actions.errors",
        description="Show lines from the ingest error log. The file is never changed.",
    )
    parser.add_argument(
        "--file", type=Path, default=None, help=f"default: $TEGEL_OUTPUT/{FILENAME}"
    )
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--run", metavar="RUN_ID", default=None)
    which.add_argument("--last", action="store_true", help="only the most recent run")
    parser.add_argument("--stage", choices=STAGES, default=None)
    parser.add_argument("--json", action="store_true", help="print matching lines unchanged")
    options = parser.parse_args(argv)
    path = options.file
    if path is None:
        output = os.environ.get("TEGEL_OUTPUT", "").strip()
        if not output:
            parser.error("TEGEL_OUTPUT is required unless --file is given")
        path = Path(output) / FILENAME
    chosen = select(read_lines(path), run=options.run, last=options.last, stage=options.stage)
    if not chosen:
        print("no errors")
        return 0
    if options.json:
        for raw, _ in chosen:
            print(raw)
    else:
        print(table([line for _, line in chosen]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
