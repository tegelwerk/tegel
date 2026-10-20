"""Origin metadata written on every node tegel-register creates."""
from __future__ import annotations

from typing import Any

ORIGIN_SPEC = {"name": "tegel-register-origin", "version": "1"}


def origin_metadata(origin: Any) -> dict:
    """JSON object stored at ``metadata["tegel_register"]``."""
    mode = origin.mode.value if hasattr(origin.mode, "value") else origin.mode
    return {
        "origin": origin.uri,
        "resolved": origin.resolved_uri,
        "trail": list(origin.trail),
        "protocol": origin.protocol,
        "mode": mode,
        "acquired_from": origin.acquired_from,
        "version": origin.binder_version,
    }
