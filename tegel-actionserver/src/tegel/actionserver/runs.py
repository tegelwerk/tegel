"""The run table: one SQLite file, the only record of runs between restarts.

A token is never stored here. ``wait`` blocks on a condition that every state
change notifies.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tegel.core.contracts.actions import FINAL_STATES, Run, RunPhase, RunState, can_transition

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    state TEXT NOT NULL,
    phase TEXT,
    inputs TEXT NOT NULL,
    parameters TEXT NOT NULL,
    principal TEXT NOT NULL,
    batch TEXT,
    submitted TEXT NOT NULL,
    started TEXT,
    ended TEXT,
    cached INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    error TEXT,
    request_key TEXT NOT NULL,
    seq INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_key ON runs (request_key, state);
CREATE INDEX IF NOT EXISTS runs_principal ON runs (principal, seq);
CREATE TABLE IF NOT EXISTS throughput (
    source TEXT PRIMARY KEY,
    bytes_per_second REAL NOT NULL,
    samples INTEGER NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_run_id() -> str:
    """A Tiled-safe key: ``r`` and 32 hex digits."""
    return "r" + uuid.uuid4().hex


class TransitionError(RuntimeError):
    """A state change the contract does not allow."""


class RunStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self.changed = threading.Condition(self._lock)
        self._seq = int(self._db.execute("SELECT COALESCE(MAX(seq), 0) FROM runs").fetchone()[0])

    # -- writes ---------------------------------------------------------------------------------

    def add(
        self,
        *,
        action: str,
        inputs: dict[str, Any],
        parameters: dict[str, Any],
        principal: str,
        request_key: str,
        batch: str | None = None,
        cached_result: str | None = None,
    ) -> Run:
        run_id = new_run_id()
        stamp = now()
        with self._lock:
            self._seq += 1
            state = RunState.succeeded if cached_result else RunState.queued
            self._db.execute(
                "INSERT INTO runs (id, action, state, inputs, parameters, principal, batch, "
                "submitted, started, ended, cached, result, request_key, seq) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, action, state.value, json.dumps(inputs), json.dumps(parameters),
                    principal, batch, stamp, stamp if cached_result else None,
                    stamp if cached_result else None, 1 if cached_result else 0, cached_result,
                    request_key, self._seq,
                ),
            )
            self.changed.notify_all()
        return self.get(run_id)

    def move(
        self,
        run_id: str,
        state: RunState,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> Run:
        """Change state, refusing transitions the contract does not list."""
        with self._lock:
            current = self.get(run_id)
            if not can_transition(current.state, state):
                raise TransitionError(f"run {run_id}: {current.state.value} -> {state.value}")
            stamp = now()
            started = stamp if state is RunState.running else current.started
            ended = stamp if state in FINAL_STATES else current.ended
            phase = RunPhase.stage.value if state is RunState.running else None
            self._db.execute(
                "UPDATE runs SET state=?, phase=?, started=?, ended=?, result=?, error=? "
                "WHERE id=?",
                (state.value, phase, started, ended, result, error, run_id),
            )
            self.changed.notify_all()
        return self.get(run_id)

    def set_phase(self, run_id: str, phase: RunPhase) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE runs SET phase=? WHERE id=? AND state=?",
                (phase.value, run_id, RunState.running.value),
            )
            self.changed.notify_all()

    def fail_unfinished(self, reason: str) -> int:
        """At startup: every queued or running run is failed (the contract's restart rule)."""
        with self._lock:
            cursor = self._db.execute(
                "UPDATE runs SET state=?, phase=NULL, ended=?, error=? WHERE state IN (?, ?)",
                (RunState.failed.value, now(), reason, RunState.queued.value,
                 RunState.running.value),
            )
            self.changed.notify_all()
            return cursor.rowcount

    # -- reads ----------------------------------------------------------------------------------

    def _query(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def get(self, run_id: str) -> Run:
        rows = self._query(
            "SELECT id, action, state, phase, inputs, parameters, principal, batch, submitted, "
            "started, ended, cached, result, error FROM runs WHERE id=?",
            (run_id,),
        )
        if not rows:
            raise KeyError(run_id)
        return _run(rows[0])

    def request_key(self, run_id: str) -> str:
        rows = self._query("SELECT request_key FROM runs WHERE id=?", (run_id,))
        if not rows:
            raise KeyError(run_id)
        return str(rows[0][0])

    def owner(self, run_id: str) -> str:
        return self.get(run_id).principal

    def list(
        self,
        *,
        principal: str | None,
        action: str | None = None,
        state: str | None = None,
        batch: str | None = None,
        limit: int = 100,
    ) -> list[Run]:
        clauses, args = [], []
        if principal is not None:
            clauses.append("principal=?")
            args.append(principal)
        if action:
            clauses.append("(action=? OR action LIKE ?)")
            args.extend([action, f"{action}@%"])
        if state:
            clauses.append("state=?")
            args.append(state)
        if batch:
            clauses.append("batch=?")
            args.append(batch)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query(
            "SELECT id, action, state, phase, inputs, parameters, principal, batch, submitted, "
            f"started, ended, cached, result, error FROM runs {where} ORDER BY seq DESC LIMIT ?",
            (*args, max(1, min(int(limit), 1000))),
        )
        return [_run(row) for row in rows]

    def cached(self, request_key: str, principal: str) -> str | None:
        """The result of the newest succeeded, uncached run with this key by this principal.

        Reuse stays within one principal: the earlier run read its inputs with that
        caller's authority, which another caller may not hold.
        """
        rows = self._query(
            "SELECT result FROM runs WHERE request_key=? AND principal=? AND state=? "
            "AND result IS NOT NULL ORDER BY seq DESC LIMIT 1",
            (request_key, principal, RunState.succeeded.value),
        )
        return str(rows[0][0]) if rows else None

    def wait(self, run_id: str, timeout: float) -> Run:
        deadline = time.monotonic() + max(0.0, timeout)
        with self.changed:
            while True:
                run = self.get(run_id)
                remaining = deadline - time.monotonic()
                if run.state in FINAL_STATES or remaining <= 0:
                    return run
                self.changed.wait(remaining)

    # -- throughput -----------------------------------------------------------------------------

    def throughput(self, source: str) -> float | None:
        rows = self._query("SELECT bytes_per_second FROM throughput WHERE source=?", (source,))
        return float(rows[0][0]) if rows else None

    def record_transfer(self, source: str, size: int, seconds: float) -> None:
        """Fold one measured transfer into the source's throughput (exponential average)."""
        if size <= 0 or seconds <= 0:
            return
        rate = size / seconds
        with self._lock:
            row = self._db.execute(
                "SELECT bytes_per_second, samples FROM throughput WHERE source=?", (source,)
            ).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO throughput (source, bytes_per_second, samples) VALUES (?, ?, 1)",
                    (source, rate),
                )
            else:
                blended = 0.7 * float(row[0]) + 0.3 * rate
                self._db.execute(
                    "UPDATE throughput SET bytes_per_second=?, samples=? WHERE source=?",
                    (blended, int(row[1]) + 1, source),
                )

    def close(self) -> None:
        self._db.close()


def _run(row: tuple) -> Run:
    return Run(
        id=row[0],
        action=row[1],
        state=RunState(row[2]),
        phase=RunPhase(row[3]) if row[3] else None,
        inputs=json.loads(row[4]),
        parameters=json.loads(row[5]),
        principal=row[6],
        batch=row[7],
        submitted=row[8],
        started=row[9],
        ended=row[10],
        cached=bool(row[11]),
        result=row[12],
        error=row[13],
    )
