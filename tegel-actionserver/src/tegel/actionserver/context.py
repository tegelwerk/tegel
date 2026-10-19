"""What an action receives besides its inputs and parameters."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class RunContext:
    """Per-run handles. The caller's token is not here: staging has already used it."""

    run_id: str
    action: str
    principal: str
    work_dir: Path
    log: logging.Logger | logging.LoggerAdapter = field(
        default_factory=lambda: logging.getLogger("tegel.actionserver.run")
    )
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def cancelled(self) -> bool:
        """True once the server has been asked to stop this run. Long actions should check it."""
        return self._cancel.is_set()
