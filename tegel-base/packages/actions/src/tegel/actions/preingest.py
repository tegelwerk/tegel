"""Pre-ingest: run the installed converters on raw files that are ready.

A raw file is ready when it matches a converter's patterns, has not changed for
``stable`` seconds, and is not in the ledger. The first converter, in entry-point
name order, whose patterns match a file claims it. Ready files are grouped per
converter and per collection (the first folder under the raw root), then by the
converter's ``group``. Each unit is converted once; the ledger
(``<output>/preingest/ledger.json``) records the outcome. A file that changes after
conversion is logged and not reconverted; a failed file is retried once it changes.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from tegel.core.plugins import Registry

log = logging.getLogger("tegel.actions.preingest")


@dataclass
class Unit:
    """Files one converter turns into records together."""

    converter: str
    collection: str | None
    files: list[Path]


@dataclass
class PreIngestResult:
    records: list[Path] = field(default_factory=list)
    converted: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)


class Ledger:
    """Which raw files were converted, with their size and mtime at the time."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        if path.is_file():
            self.entries = json.loads(path.read_text(encoding="utf-8"))

    def get(self, file: Path) -> dict[str, Any] | None:
        return self.entries.get(str(file))

    def record(self, file: Path, status: str, **fields: Any) -> None:
        stat = file.stat()
        self.entries[str(file)] = {
            "status": status, "size": stat.st_size, "mtime": stat.st_mtime,
            "at": datetime.now(UTC).isoformat(timespec="seconds"), **fields,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.entries, indent=1, sort_keys=True), "utf-8")
        os.replace(temporary, self.path)


def raw_roots(value: str = "") -> list[Path]:
    """``value`` or ``TEGEL_RAW``: raw directories separated by ``os.pathsep``."""
    text = value or os.environ.get("TEGEL_RAW", "")
    roots = [Path(item.strip()) for item in text.split(os.pathsep) if item.strip()]
    for root in roots:
        if not root.is_absolute():
            raise RuntimeError(f"TEGEL_RAW entries must be absolute: {root}")
    return roots


def converter_config(path: str = "") -> dict[str, Any]:
    """The converters configuration: YAML keyed by converter entry-point name."""
    text = path or os.environ.get("TEGEL_CONVERTERS_CONFIG", "")
    if not text.strip():
        return {}
    loaded = yaml.safe_load(Path(text).read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise RuntimeError("TEGEL_CONVERTERS_CONFIG must hold a mapping")
    return loaded


def pending(roots: Iterable[Path], converters: dict[str, Any], ledger: Ledger, *,
            stable: float, now: float | None = None) -> list[Unit]:
    """Units of ready, not yet converted files."""
    now = time.time() if now is None else now
    order = sorted(converters)
    buckets: dict[tuple[str, str | None], list[Path]] = {}
    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            log.warning("raw directory not found: %s", root)
            continue
        for file in sorted(path for path in root.rglob("*") if path.is_file()):
            relative = file.relative_to(root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            name = next((n for n in order
                         if any(fnmatch.fnmatch(file.name, p) for p in converters[n].patterns)),
                        None)
            if name is None:
                continue
            entry = ledger.get(file)
            if entry is not None:
                stat = file.stat()
                if entry.get("size") == stat.st_size and entry.get("mtime") == stat.st_mtime:
                    continue
                if entry.get("status") == "done":
                    if not entry.get("changed_reported"):
                        log.warning("%s changed after conversion; not reconverting", file)
                        entry["changed_reported"] = True
                    continue
            if now - file.stat().st_mtime < stable:
                continue
            collection = relative.parts[0] if len(relative.parts) > 1 else None
            buckets.setdefault((name, collection), []).append(file)
    units: list[Unit] = []
    for (name, collection), files in sorted(buckets.items(),
                                            key=lambda item: (item[0][0], item[0][1] or "")):
        for group in converters[name].group(files):
            units.append(Unit(converter=name, collection=collection,
                              files=[Path(item) for item in group]))
    return units


def convert_unit(converter: Any, unit: Unit, records: Path,
                 config: dict[str, Any]) -> list[Path]:
    """Convert one unit and check that every record file lies under ``records``."""
    options = {"collection": unit.collection, **(config.get(unit.converter) or {})}
    written = [Path(item) for item in converter.convert(unit.files, records, options)]
    outside = [str(item) for item in written
               if not item.resolve().is_relative_to(records.resolve())]
    if outside:
        raise RuntimeError(f"converter wrote records outside {records}: {outside}")
    return written


def record_outcome(ledger: Ledger, result: PreIngestResult, unit: Unit,
                   written: list[Path] | None, error: Exception | None) -> None:
    files = [str(f) for f in unit.files]
    if error is not None:
        log.error("convert failed (%s): %s: %s", unit.converter, files, error)
        result.failed.append({"converter": unit.converter, "files": files,
                              "error": str(error)[:500]})
        for file in unit.files:
            ledger.record(file, "failed", converter=unit.converter, error=str(error)[:500])
    else:
        written = written or []
        log.info("converted (%s) %s -> %d record(s)", unit.converter, files, len(written))
        result.records.extend(written)
        result.converted.append({"converter": unit.converter, "collection": unit.collection,
                                 "files": files, "records": [str(r) for r in written]})
        for file in unit.files:
            ledger.record(file, "done", converter=unit.converter,
                          records=[str(r) for r in written])
    ledger.save()


def ledger_for(output: Path) -> Ledger:
    return Ledger(output / "preingest" / "ledger.json")


def convert_ready(roots: list[Path], records: Path, output: Path, *, stable: float = 60.0,
                  registry: Registry | None = None,
                  config: dict[str, Any] | None = None) -> PreIngestResult:
    """Convert every ready unit; a failing unit is recorded and does not stop the others."""
    converters = (registry or Registry()).converters
    result = PreIngestResult()
    if not converters or not roots:
        return result
    config = converter_config() if config is None else config
    ledger = ledger_for(output)
    records.mkdir(parents=True, exist_ok=True)
    for unit in pending(roots, converters, ledger, stable=stable):
        try:
            written = convert_unit(converters[unit.converter], unit, records, config)
        except Exception as exc:
            record_outcome(ledger, result, unit, None, exc)
        else:
            record_outcome(ledger, result, unit, written, None)
    ledger.save()
    return result
