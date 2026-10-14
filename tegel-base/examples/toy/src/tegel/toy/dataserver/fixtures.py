"""Write toy record directories for tests. Needs ``zarr`` when ``frames`` is written."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NOTES = "toy notes: a plain text part\n"
STATS = "x,y\n1,2.5\n3,4.5\n"


def write_record(
    folder: Path,
    record: dict[str, Any],
    *,
    frames: bool = True,
    stats: bool = True,
    notes: bool = True,
) -> Path:
    """Write ``record.json`` and the chosen parts into ``folder``; return the marker path."""
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / "record.json"
    marker.write_text(json.dumps(record), encoding="utf-8")
    if frames:
        import numpy as np
        import zarr

        array = zarr.create_array(
            folder / "frames.zarr", shape=(4, 3), chunks=(2, 3), dtype="i4", overwrite=True
        )
        array[:] = np.arange(12, dtype="i4").reshape(4, 3)
    if stats:
        (folder / "stats.csv").write_text(STATS, encoding="utf-8")
    if notes:
        (folder / "notes.txt").write_text(NOTES, encoding="utf-8")
    return marker
