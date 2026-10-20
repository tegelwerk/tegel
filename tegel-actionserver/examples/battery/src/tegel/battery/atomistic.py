"""ASE-driven atomistic actions with pluggable calculators.

Calculators load once per process and are reused, so a universal MLIP is not
reloaded for every run. Select one with the ``calculator`` parameter:

    emt             ASE EMT (metals only; for tests)
    mace-mp[:size]  MACE-MP foundation model (mace-torch), size small|medium|large
    uma[:task]      Meta UMA via fairchem (task omat|omol|...; needs a HF token)
    orb             Orb v3 (orb-models)
    chgnet          CHGNet

Both actions hold the ``gpu`` resource, so a server with ``TEGEL_ACTION_RESOURCES=gpu=1``
runs one at a time.
"""

from __future__ import annotations

import functools
import threading

import numpy as np

from tegel.actionserver import Quantity, action, port
from tegel.battery.trajectory import Trajectory, load_structure

S = port("structure", "structure", description="CIF, POSCAR or pymatgen JSON")
_LOCK = threading.Lock()


def _device() -> str:
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


@functools.cache
def make_calculator(spec: str):
    name, _, arg = spec.partition(":")
    if name == "emt":
        from ase.calculators.emt import EMT

        return EMT()
    if name == "mace-mp":
        from mace.calculators import mace_mp

        return mace_mp(model=arg or "small", device=_device(), default_dtype="float64")
    if name == "uma":
        from fairchem.core import FAIRChemCalculator, pretrained_mlip

        predictor = pretrained_mlip.get_predict_unit("uma-s-1p1", device=_device())
        return FAIRChemCalculator(predictor, task_name=arg or "omat")
    if name == "orb":
        from orb_models.forcefield import pretrained
        from orb_models.forcefield.calculator import ORBCalculator

        model = pretrained.orb_v3_conservative_inf_omat(device=_device())
        return ORBCalculator(model, device=_device())
    if name == "chgnet":
        from chgnet.model.dynamics import CHGNetCalculator

        return CHGNetCalculator()
    raise ValueError(f"unknown calculator {spec!r}")


def _atoms(structure, calculator: str):
    from pymatgen.io.ase import AseAtomsAdaptor

    atoms = AseAtomsAdaptor.get_atoms(load_structure(structure))
    with _LOCK:
        atoms.calc = make_calculator(calculator)
    return atoms


@action(
    "ase.relax", version="0.1.0", inputs=[S],
    outputs=[S, port("energy_per_atom", "quantity", unit="eV/atom"),
             port("volume_change", "quantity", unit="fraction"),
             port("steps", "quantity", unit="count"),
             port("log", "file", description="FIRE optimiser log")],
    resources=["gpu"], software=["ase", "pymatgen"], license="BSD-3-Clause",
    validity="MLIP results inherit the model's domain of validity; check the calculator's "
             "training data. Lattice constants should be reported after a Selling/Delaunay "
             "reduction; fmax=0.05 leaves roughly 1 GPa residual stress.",
)
def relax(ctx, structure, *, calculator: str = "mace-mp", fmax: float = 0.05,
          max_steps: int = 500, relax_cell: bool = True):
    """Relax atomic positions (and optionally the cell) with FIRE."""
    from ase.filters import FrechetCellFilter
    from ase.optimize import FIRE
    from pymatgen.io.ase import AseAtomsAdaptor

    atoms = _atoms(structure, calculator)
    v0 = atoms.get_volume()
    target = FrechetCellFilter(atoms) if relax_cell else atoms
    log = ctx.work_dir / "relax.log"
    optimizer = FIRE(target, logfile=str(log))
    converged = optimizer.run(fmax=fmax, steps=max_steps)
    ctx.log.info("converged=%s steps=%d", converged, optimizer.nsteps)
    if not converged:
        raise RuntimeError(f"not converged to fmax={fmax} in {max_steps} steps")
    conditions = {"calculator": calculator, "fmax": fmax, "relax_cell": relax_cell}
    return {
        "structure": AseAtomsAdaptor.get_structure(atoms),
        "energy_per_atom": Quantity(atoms.get_potential_energy() / len(atoms),
                                    conditions=conditions),
        "volume_change": Quantity(atoms.get_volume() / v0 - 1.0, conditions=conditions),
        "steps": Quantity(float(optimizer.nsteps)),
        "log": log,
    }


@action(
    "ase.md", version="0.1.0", inputs=[S],
    outputs=[port("trajectory", "trajectory", description=".npz, unwrapped positions"),
             port("mean_temperature", "quantity", unit="K"),
             port("energy_drift", "quantity", unit="eV/atom/ps")],
    resources=["gpu"], software=["ase", "pymatgen"], license="BSD-3-Clause",
)
def md(ctx, structure, *, calculator: str = "mace-mp", temperature_K: float = 600.0,
       timestep_fs: float = 2.0, steps: int = 5000, save_every: int = 10,
       friction: float = 0.01, seed: int = 0):
    """NVT Langevin molecular dynamics; stores unwrapped positions every ``save_every`` steps."""
    from ase import units
    from ase.constraints import FixCom
    from ase.md.langevin import Langevin
    from ase.md.velocitydistribution import MaxwellBoltzmannDistribution, Stationary

    atoms = _atoms(structure, calculator)
    rng = np.random.default_rng(seed)
    MaxwellBoltzmannDistribution(atoms, temperature_K=temperature_K, rng=rng)
    Stationary(atoms)
    atoms.set_constraint(FixCom())  # a fixed centre of mass keeps drift out of the MSD
    dyn = Langevin(atoms, timestep_fs * units.fs, temperature_K=temperature_K,
                   friction=friction, rng=rng, fixcm=False)
    frames, temps, etot = [], [], []

    def record():
        if ctx.cancelled():
            raise RuntimeError("cancelled")
        frames.append(atoms.get_positions().copy())  # ASE does not wrap during MD
        temps.append(atoms.get_temperature())
        etot.append(atoms.get_total_energy() / len(atoms))

    dyn.attach(record, interval=save_every)
    dyn.run(steps)
    t_ps = np.arange(len(etot)) * save_every * timestep_fs / 1000.0
    drift = float(np.polyfit(t_ps, etot, 1)[0]) if len(etot) > 2 else 0.0
    conditions = {"calculator": calculator, "temperature_K": temperature_K,
                  "ensemble": "NVT-Langevin"}
    trajectory = Trajectory(
        positions=np.array(frames), numbers=atoms.get_atomic_numbers(), cell=atoms.cell.array,
        timestep_fs=timestep_fs * save_every, temperature_K=temperature_K,
        extra={"calculator": calculator, "md_timestep_fs": timestep_fs},
    )
    path = trajectory.save(ctx.work_dir / "trajectory.npz")
    return {"trajectory": path,
            "mean_temperature": Quantity(float(np.mean(temps)), conditions=conditions),
            "energy_drift": Quantity(drift, conditions=conditions)}
