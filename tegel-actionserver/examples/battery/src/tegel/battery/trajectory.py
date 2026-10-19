"""The ``.npz`` trajectory format the MD and transport actions exchange."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class Trajectory:
    positions: np.ndarray  # (frames, atoms, 3) Angstrom, unwrapped
    numbers: np.ndarray  # (atoms,) atomic numbers
    cell: np.ndarray  # (3, 3) Angstrom
    timestep_fs: float  # time between stored frames
    temperature_K: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def symbols(self) -> list[str]:
        from ase.data import chemical_symbols

        return [chemical_symbols[int(z)] for z in self.numbers]

    def save(self, path: Path) -> Path:
        np.savez_compressed(
            path, positions=self.positions, numbers=self.numbers, cell=self.cell,
            timestep_fs=self.timestep_fs,
            temperature_K=np.nan if self.temperature_K is None else self.temperature_K,
            extra=json.dumps(self.extra),
        )
        return path

    @classmethod
    def load(cls, path: Path) -> Trajectory:
        with np.load(path, allow_pickle=False) as found:
            temperature = float(found["temperature_K"])
            return cls(
                positions=found["positions"], numbers=found["numbers"], cell=found["cell"],
                timestep_fs=float(found["timestep_fs"]),
                temperature_K=None if np.isnan(temperature) else temperature,
                extra=json.loads(str(found["extra"])),
            )


def load_structure(path: Path):
    """A pymatgen Structure from a file: pymatgen JSON, or anything Structure.from_file reads."""
    from pymatgen.core import Structure

    path = Path(path)
    if path.suffix == ".json":
        return Structure.from_dict(json.loads(path.read_text(encoding="utf-8")))
    return Structure.from_file(str(path))
