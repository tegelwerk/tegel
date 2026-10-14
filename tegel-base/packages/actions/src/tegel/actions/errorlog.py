"""Append one JSON line per bad record to the ingest error log."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

LOGGER = logging.getLogger("tegel.staging")
MESSAGE_LIMIT = 2000
FILENAME = "ingest-errors.jsonl"


class ErrorLog:
    """Write bad-record lines to ``path`` and the ``tegel.staging`` logger.

    With ``path`` set to ``None`` only the logger is used.
    """

    def __init__(self, path: Path | None, *, run_id: str, mode: str) -> None:
        self.path = path
        self.run_id = run_id
        self.mode = mode

    def record(
        self,
        *,
        stage: str,
        path: str,
        exc: BaseException,
        plugin_id: str | None = None,
        resource_id: str | None = None,
    ) -> dict[str, object]:
        """Write one line for a bad record and return it."""
        line = {
            "time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run_id": self.run_id,
            "mode": self.mode,
            "stage": stage,
            "path": path,
            "plugin_id": plugin_id,
            "resource_id": resource_id,
            "error": type(exc).__name__,
            "message": one_line(str(exc)),
        }
        level = logging.ERROR if self.mode == "fail" else logging.WARNING
        LOGGER.log(level, "%s: %s: %s: %s", stage, path, line["error"], line["message"])
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(line, ensure_ascii=False) + "\n"
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
        return line


def one_line(text: str) -> str:
    """Replace newlines with spaces and cut the text at ``MESSAGE_LIMIT`` characters."""
    flat = " ".join(text.splitlines())
    if len(flat) > MESSAGE_LIMIT:
        return flat[:MESSAGE_LIMIT] + "…"
    return flat
