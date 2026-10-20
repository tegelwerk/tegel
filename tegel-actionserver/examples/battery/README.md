# tegel-battery-actions

An example incarnation package for the Tegel action server: battery-materials actions ported from actionbank. Installing it next to `tegel-actionserver` registers them on the `tegel.actions` entry-point group.

| Action | Inputs | Outputs |
| --- | --- | --- |
| `xrd.powder` | structure | `pattern` (table), `strongest_peak` |
| `xrd.match` | structure, measured pattern (table) | `peaks` (table), `fraction_matched`, `d_scale`, `residual_shift`, `unexplained_peaks` |
| `ase.relax` | structure | relaxed structure, `energy_per_atom`, `volume_change`, `steps`, optimiser log |
| `ase.md` | structure | trajectory (`.npz`), `mean_temperature`, `energy_drift` |
| `transport.msd_diffusion` | trajectory | `msd` (table), `diffusion_coefficient` |

Structures are files (CIF, POSCAR or pymatgen JSON); structure outputs are pymatgen JSON. Trajectories use the `.npz` layout in `tegel.battery.trajectory`. `ase.relax` and `ase.md` hold the `gpu` resource. Their `calculator` parameter selects EMT (tests), MACE-MP (`pip install tegel-battery-actions[mace]`), UMA, Orb or CHGNet.

```bash
cd tegel-actionserver/examples/battery && uv sync --group dev && uv run pytest
```
