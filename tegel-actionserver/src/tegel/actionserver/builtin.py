"""Two small generic actions, so a fresh server has something to run.

Domain actions belong to an incarnation's own package.
"""

from __future__ import annotations

from tegel.actionserver.authoring import Quantity, action, port


@action(
    "array.stats",
    version="0.1.0",
    inputs=[port("data", "array", description="any numeric array")],
    outputs=[
        port("mean", "quantity"),
        port("std", "quantity"),
        port("histogram", "table", description="bin_left, bin_right, count"),
        port("summary", "json"),
    ],
    software=["numpy"],
    license="BSD-3-Clause",
    validity="Ignores NaN values. std is the population standard deviation.",
)
def array_stats(ctx, data, *, bins: int = 20, unit: str | None = None):
    """Mean, standard deviation and histogram of an array's finite values."""
    import numpy as np
    import pandas as pd

    values = np.asarray(data, dtype=float).ravel()
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError("the array has no finite values")
    counts, edges = np.histogram(finite, bins=bins)
    ctx.log.info("array.stats over %d values", finite.size)
    return {
        "mean": Quantity(float(finite.mean()), unit=unit, method="arithmetic mean",
                         uncertainty=float(finite.std() / np.sqrt(finite.size))),
        "std": Quantity(float(finite.std()), unit=unit, method="population standard deviation"),
        "histogram": pd.DataFrame({"bin_left": edges[:-1], "bin_right": edges[1:],
                                   "count": counts}),
        "summary": {"shape": list(np.shape(data)), "finite": int(finite.size),
                    "min": float(finite.min()), "max": float(finite.max())},
    }


@action(
    "table.summary",
    version="0.1.0",
    inputs=[port("table", "table")],
    outputs=[port("summary", "table"), port("rows", "quantity")],
    software=["pandas"],
    license="BSD-3-Clause",
)
def table_summary(ctx, table, *, columns: list[str] | None = None):
    """Per-column count, mean, std, min, quartiles and max of a table's numeric columns."""
    frame = table if columns is None else table[columns]
    summary = frame.describe().transpose().reset_index(names="column")
    return {"summary": summary, "rows": Quantity(float(len(table)), method="row count")}
