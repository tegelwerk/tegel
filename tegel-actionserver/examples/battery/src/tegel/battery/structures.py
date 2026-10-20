"""Structure analysis: simulated and matched powder diffraction."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from tegel.actionserver import Quantity, action, port
from tegel.battery.trajectory import load_structure

S = port("structure", "structure", description="CIF, POSCAR or pymatgen JSON")


def _structure(value):
    return load_structure(value) if isinstance(value, (str, Path)) else value


@action(
    "xrd.powder",
    version="0.1.0",
    inputs=[S],
    outputs=[
        port("pattern", "table", description="two_theta, intensity, d_spacing, hkl"),
        port("strongest_peak", "quantity", unit="deg"),
    ],
    software=["pymatgen"],
    license="BSD-3-Clause",
)
def xrd_powder(
    ctx,
    structure,
    *,
    wavelength: str = "CuKa",
    two_theta_max: float = 90.0,
    conventional: bool = True,
):
    """Simulated powder X-ray diffraction pattern (hkl indexed on the conventional cell)."""
    structure = _structure(structure)
    from pymatgen.analysis.diffraction.xrd import XRDCalculator
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

    if conventional:
        structure = SpacegroupAnalyzer(structure).get_conventional_standard_structure()
    pat = XRDCalculator(wavelength=wavelength).get_pattern(
        structure, two_theta_range=(5, two_theta_max)
    )
    df = pd.DataFrame(
        {
            "two_theta": pat.x,
            "intensity": pat.y,
            "d_spacing": pat.d_hkls,
            "hkl": [";".join(str(tuple(h["hkl"])) for h in hk) for hk in pat.hkls],
        }
    )
    top = float(df.loc[df.intensity.idxmax(), "two_theta"])
    return {
        "pattern": df,
        "strongest_peak": Quantity(top, "deg", conditions={"wavelength": wavelength}),
    }


@action(
    "xrd.match",
    version="0.2.0",
    inputs=[
        S,
        port(
            "measured",
            "table",
            description="measured powder pattern: first column 2theta in degrees, second intensity",
        ),
    ],
    outputs=[
        port(
            "peaks",
            "table",
            description="calculated reflections and the measured peak matched to each",
        ),
        port("fraction_matched", "quantity", unit="fraction"),
        port("d_scale", "quantity", unit="ratio"),
        port("residual_shift", "quantity", unit="deg", optional=True),
        port("unexplained_peaks", "quantity", unit="count"),
    ],
    software=["pymatgen", "scipy"],
    license="BSD-3-Clause",
    validity="Peak-position check, not a Rietveld fit. d_scale is the uniform factor "
    "d_measured/d_model that best aligns the peaks: for an isotropic change it is "
    "a_measured/a_model; anisotropic strain or a zero-point error shows up as "
    "residual_shift. Unexplained peaks point to another phase.",
)
def xrd_match(
    ctx,
    structure,
    measured,
    *,
    wavelength: str = "CuKa",
    tolerance_deg: float = 0.1,
    max_scale_change: float = 0.05,
    min_relative_intensity: float = 5.0,
    prominence: float = 0.03,
):
    """Check a structure against a measured powder pattern: fit a d-spacing scale, match peaks."""
    structure = _structure(structure)
    from pymatgen.analysis.diffraction.xrd import XRDCalculator
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    from scipy.signal import find_peaks

    cols = list(measured.columns)
    x = np.asarray(measured[cols[0]], dtype=float)
    y = np.asarray(measured[cols[1]], dtype=float)
    order = np.argsort(x)
    x, y = x[order], y[order]
    yn = (y - y.min()) / (np.ptp(y) or 1.0)
    idx, _ = find_peaks(yn, prominence=prominence)
    obs = x[idx]
    if len(obs) == 0:
        raise ValueError(
            "no peaks found in the measured pattern; lower `prominence` or check the input columns"
        )
    xrd = XRDCalculator(wavelength=wavelength)
    lam = xrd.wavelength
    conv = SpacegroupAnalyzer(structure).get_conventional_standard_structure()
    lo = max(1.0, float(x.min()) - 10.0)
    hi = min(
        179.0, float(x.max()) + 10.0
    )  # wide enough that rescaled peaks can move into the window
    pat = xrd.get_pattern(conv, two_theta_range=(lo, hi))
    keep = [k for k, i in enumerate(pat.y) if i >= min_relative_intensity]
    d_calc = np.array([pat.d_hkls[k] for k in keep])
    i_calc = np.array([pat.y[k] for k in keep])
    hkls = [";".join(str(tuple(h["hkl"])) for h in pat.hkls[k]) for k in keep]

    def tt(d):  # Bragg: 2theta in degrees for d-spacings, nan where unreachable
        s_ = lam / (2.0 * d)
        return np.where(s_ < 1.0, 2.0 * np.degrees(np.arcsin(np.clip(s_, -1, 1))), np.nan)

    def matches(scale):
        t = tt(d_calc * scale)
        inside = (t >= x.min()) & (t <= x.max())
        dist = np.abs(obs[None, :] - t[:, None])
        j = np.nanargmin(np.where(np.isnan(dist), np.inf, dist), axis=1)
        hit = inside & (np.abs(obs[j] - t) <= tolerance_deg)
        return t, j, hit, inside

    # coarse search for the scale matching the most peaks (ties: smallest distance), then refine
    best = None
    for sc in np.linspace(1 - max_scale_change, 1 + max_scale_change, 2001):
        t, j, hit, inside = matches(sc)
        score = (int(hit.sum()), -float(np.nansum(np.abs(obs[j] - t)[hit])))
        if best is None or score > best[0]:
            best = (score, sc)
    scale = best[1]
    for _ in range(3):  # least squares on matched pairs, in d-space: d_obs = scale * d_calc
        t, j, hit, inside = matches(scale)
        if hit.sum() < 1:
            break
        d_obs = lam / (2.0 * np.sin(np.radians(obs[j[hit]] / 2.0)))
        scale = float(np.sum(d_obs * d_calc[hit]) / np.sum(d_calc[hit] ** 2))
    t, j, hit, inside = matches(scale)
    t0 = tt(d_calc)
    rows = [
        {
            "hkl": hkls[k],
            "intensity_calc": float(i_calc[k]),
            "two_theta_model": float(t0[k]),
            "two_theta_scaled": float(t[k]),
            "two_theta_obs": float(obs[j[k]]) if hit[k] else None,
            "residual": float(obs[j[k]] - t[k]) if hit[k] else None,
        }
        for k in range(len(d_calc))
        if inside[k]
    ]
    df = pd.DataFrame(rows)
    n_in, n_hit = int(inside.sum()), int(hit.sum())
    resid = np.array([r["residual"] for r in rows if r["residual"] is not None])
    unexplained = len(obs) - len(set(j[hit].tolist()))
    scale_err = None
    if n_hit > 1:
        d_obs = lam / (2.0 * np.sin(np.radians(obs[j[hit]] / 2.0)))
        scale_err = float(np.std(d_obs / d_calc[hit], ddof=1) / np.sqrt(n_hit))
    ctx.log.info(
        "scale %.5f, %d/%d reflections matched, %d unexplained peaks",
        scale,
        n_hit,
        n_in,
        unexplained,
    )
    cond = {"wavelength": wavelength, "tolerance_deg": tolerance_deg}
    return {
        "peaks": df,
        "fraction_matched": Quantity(n_hit / n_in if n_in else 0.0, conditions=cond),
        "d_scale": Quantity(
            scale,
            uncertainty=scale_err,
            method="least squares d_obs = s * d_model over matched peaks",
            conditions=cond,
        ),
        "residual_shift": Quantity(
            float(resid.mean()),
            uncertainty=float(resid.std(ddof=1) / np.sqrt(len(resid))) if len(resid) > 1 else None,
            method="mean(observed - scaled model) over matched peaks",
            conditions=cond,
        )
        if len(resid)
        else None,
        "unexplained_peaks": Quantity(float(unexplained), conditions=cond),
    }
