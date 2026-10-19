"""Run jobs on a pool of worker threads, honouring named resource slots.

A job names the resources it holds (``gpu``, ``catalog-writer``). It starts only
when a worker and every one of its resources are free, so a job waiting for a
GPU does not hold a worker. Jobs that can start run in submission order.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

_log = logging.getLogger("tegel.actionserver.executor")


@dataclass
class _Job:
    key: str
    resources: tuple[str, ...]
    fn: Callable[[], None]
    cancel: threading.Event = field(default_factory=threading.Event)


class Executor:
    def __init__(self, workers: int, slots: dict[str, int] | None = None) -> None:
        self._workers = workers
        self._slots = dict(slots or {})
        self._busy: dict[str, int] = {}
        self._running = 0
        self._pending: list[_Job] = []
        self._active: dict[str, _Job] = {}
        self._cond = threading.Condition()
        self._closed = False
        self._dispatcher = threading.Thread(target=self._dispatch, name="tegel-dispatch",
                                            daemon=True)
        self._dispatcher.start()

    def submit(self, key: str, resources: list[str], fn: Callable[[], None]) -> threading.Event:
        """Queue ``fn``. Returns the event that ``cancel_running`` would set."""
        job = _Job(key, tuple(sorted(set(resources))), fn)
        with self._cond:
            if self._closed:
                raise RuntimeError("executor is closed")
            self._pending.append(job)
            self._cond.notify_all()
        return job.cancel

    def cancel(self, key: str) -> bool:
        """Remove a job that has not started. False if it started or is unknown."""
        with self._cond:
            for index, job in enumerate(self._pending):
                if job.key == key:
                    del self._pending[index]
                    return True
        return False

    def is_pending(self, key: str) -> bool:
        with self._cond:
            return any(job.key == key for job in self._pending)

    def _capacity(self, resource: str) -> int:
        return self._slots.get(resource, 1)

    def _can_start(self, job: _Job) -> bool:
        if self._running >= self._workers:
            return False
        return all(self._busy.get(r, 0) < self._capacity(r) for r in job.resources)

    def _dispatch(self) -> None:
        while True:
            with self._cond:
                while not self._closed:
                    ready = next((job for job in self._pending if self._can_start(job)), None)
                    if ready is not None:
                        break
                    self._cond.wait()
                if self._closed:
                    return
                self._pending.remove(ready)
                self._running += 1
                for r in ready.resources:
                    self._busy[r] = self._busy.get(r, 0) + 1
                self._active[ready.key] = ready
            threading.Thread(target=self._run, args=(ready,), name=f"tegel-run-{ready.key}",
                             daemon=True).start()

    def _run(self, job: _Job) -> None:
        try:
            job.fn()
        except Exception:  # the job records its own failure; this is a last resort
            _log.exception("job %s raised", job.key)
        finally:
            with self._cond:
                self._running -= 1
                for r in job.resources:
                    self._busy[r] -= 1
                self._active.pop(job.key, None)
                self._cond.notify_all()

    def close(self, timeout: float = 10.0) -> list[str]:
        """Stop dispatching. Signals running jobs and returns the keys of pending ones."""
        with self._cond:
            self._closed = True
            dropped = [job.key for job in self._pending]
            self._pending.clear()
            for job in self._active.values():
                job.cancel.set()
            self._cond.notify_all()
        self._dispatcher.join(timeout)
        return dropped

    def idle(self, timeout: float) -> bool:
        """Wait until nothing is pending or running."""
        with self._cond:
            return self._cond.wait_for(lambda: not self._pending and not self._running, timeout)
