"""Ion transport from trajectories."""

from __future__ import annotations

import numpy as np
import pandas as pd

from tegel.actionserver import Quantity, action, port
from tegel.battery.trajectory import Trajectory


def msd_time_averaged(pos: np.ndarray, max_lag: int) -> np.ndarray:
    """MSD averaged over atoms and all time origins, for lags 0..max_lag (A^2)."""
    out = np.zeros(max_lag + 1)
    for lag in range(1, max_lag + 1):
        d = pos[lag:] - pos[:-lag]
        out[lag] = (d ** 2).sum(axis=2).mean()
    return out


def _slope(pos: np.ndarray, dt_ps: float, max_lag_fraction: float, fit_start_fraction: float):
    max_lag = max(2, int(len(pos) * max_lag_fraction))
    msd = msd_time_averaged(pos, max_lag)
    t = np.arange(max_lag + 1) * dt_ps
    i0 = max(1, int(max_lag * fit_start_fraction))
    if max_lag - i0 < 3:
        raise ValueError(f"only {len(pos)} usable frames; need a longer trajectory to fit the MSD")
    return float(np.polyfit(t[i0:], msd[i0:], 1)[0]), t, msd, i0


@action(
    "transport.msd_diffusion", version="0.3.0",
    inputs=[port("trajectory", "trajectory", description=".npz from ase.md")],
    outputs=[port("msd", "table", description="time_ps, msd_A2"),
             port("diffusion_coefficient", "quantity", unit="cm^2/s")],
    software=["numpy"], license="BSD-3-Clause",
    validity="Tracer D as the mean of MSD fits over independent trajectory blocks, with the "
             "standard error across blocks. Refuses to report D when it is not significantly "
             "positive (run too short or not diffusive). Prefer a Bayesian estimator (kinisi) "
             "for production.",
)
def msd_diffusion(ctx, trajectory, *, species: str = "Li", skip_fraction: float = 0.2,
                  max_lag_fraction: float = 0.5, fit_start_fraction: float = 0.2,
                  n_blocks: int = 5, min_significance: float = 2.0):
    """Tracer diffusion coefficient of one species from the mean squared displacement."""
    if not isinstance(trajectory, Trajectory):
        trajectory = Trajectory.load(trajectory)
    mask = np.array([s == species for s in trajectory.symbols])
    if not mask.any():
        raise ValueError(f"no {species} atoms in trajectory")
    pos = trajectory.positions[int(len(trajectory.positions) * skip_fraction):, mask, :]
    dt_ps = trajectory.timestep_fs / 1000.0
    _, t, msd, i0 = _slope(pos, dt_ps, max_lag_fraction, fit_start_fraction)
    # D and its error both come from independent blocks: fit errors on overlapping MSD points
    # are far too small, and a full-run estimate with a block error understates the error.
    min_block = 20
    k = min(n_blocks, len(pos) // min_block)
    if k < 3:
        raise ValueError(f"{len(pos)} usable frames; need at least {3 * min_block} "
                         f"(3 blocks of {min_block}) for a diffusion estimate with an uncertainty")
    slopes = [_slope(b, dt_ps, max_lag_fraction, fit_start_fraction)[0]
              for b in np.array_split(pos, k)]
    slope = float(np.mean(slopes))
    slope_err = float(np.std(slopes, ddof=1) / np.sqrt(k))
    ctx.log.info("MSD slope %.4g +- %.2g A^2/ps over %.3f ps (%d frames, %d blocks)",
                 slope, slope_err, t[-1], len(pos), k)
    if slope <= min_significance * slope_err:
        raise RuntimeError(
            f"MSD not diffusive: slope {slope:.3g} +- {slope_err:.2g} A^2/ps over {t[-1]:.2f} ps "
            "is not significantly positive; run longer or hotter")
    to_cm2s = 1e-16 / 1e-12  # A^2/ps -> cm^2/s
    conditions = {"species": species, "temperature_K": trajectory.temperature_K,
                  "calculator": trajectory.extra.get("calculator"),
                  "fit_window_ps": [float(t[i0]), float(t[-1])]}
    return {
        "msd": pd.DataFrame({"time_ps": t, "msd_A2": msd}),
        "diffusion_coefficient": Quantity(
            slope / 6.0 * to_cm2s, uncertainty=slope_err / 6.0 * to_cm2s,
            method=f"mean of linear fits to time-origin-averaged tracer MSD over {k} independent "
                   "blocks; standard error across blocks",
            conditions=conditions),
    }
