"""Actions the tests run. They are passed to create_app directly, not installed."""

from __future__ import annotations

import threading
import time

import numpy as np

from tegel.actionserver import Quantity, action, port

SPANS: list[tuple[str, float, float]] = []
SPANS_LOCK = threading.Lock()


@action("test.sleep", version="1", outputs=[port("slept", "quantity", unit="s")])
def sleep(ctx, *, seconds: float = 0.2, tag: str = ""):
    """Sleep, and record when."""
    start = time.monotonic()
    while time.monotonic() - start < seconds and not ctx.cancelled():
        time.sleep(0.01)
    with SPANS_LOCK:
        SPANS.append((tag, start, time.monotonic()))
    return {"slept": seconds}


@action("test.gpu", version="1", resources=["gpu"], outputs=[port("slept", "quantity")])
def gpu(ctx, *, seconds: float = 0.2, tag: str = ""):
    """Hold the gpu slot while sleeping."""
    return sleep(ctx, seconds=seconds, tag=tag)


@action("test.fail", version="1", inputs=[port("data", "array")],
        outputs=[port("never", "quantity")])
def fail(ctx, data):
    """Always raise."""
    raise ValueError("boom")


@action("test.undeclared", version="1", outputs=[port("a", "json")])
def undeclared(ctx):
    """Return an output the card does not declare."""
    return {"a": {}, "b": 1}


@action("test.make", version="1",
        outputs=[port("values", "array"), port("info", "json"), port("notes", "file"),
                 port("cell", "structure")])
def make(ctx, *, n: int = 6):
    """Make an array, JSON, a text file and a structure-like dict."""
    notes = ctx.work_dir / "notes.txt"
    notes.write_text("hello\n")
    return {"values": np.arange(n, dtype="f8").reshape(2, -1), "info": {"n": n},
            "notes": notes, "cell": {"lattice": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}}


@action("test.ingest", version="1", scope="catalog:write", resources=["catalog-writer"],
        outputs=[port("count", "quantity")])
def ingest(ctx):
    """Stand-in catalog action."""
    return {"count": Quantity(0.0)}


@action("test.read", version="1", inputs=[port("data", "array")],
        outputs=[port("total", "quantity")])
def read(ctx, data):
    """Sum an array input."""
    return {"total": float(np.asarray(data).sum())}


@action("test.json", version="1", inputs=[port("doc", "json")], outputs=[port("keys", "json")])
def read_json(ctx, doc):
    """Keys of a JSON input."""
    return {"keys": sorted(doc)}


@action("test.many", version="1", inputs=[port("items", "selection")],
        outputs=[port("count", "quantity")])
def many(ctx, items):
    """Count a selection."""
    return {"count": float(len(items))}


ALL = [sleep, gpu, fail, undeclared, make, ingest, read, read_json, many]
