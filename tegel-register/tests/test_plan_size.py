"""A plan says how many bytes the node holds and whether executing it copies them."""

from pathlib import Path

import numpy as np

from tegel.register.binders.tiled import _node_size
from tegel.register.bind import Acquisition, BinderPlan, _plan_size
from tegel.register.binders.file import _local_size


def test_local_size_of_file_and_tree(tmp_path: Path) -> None:
    (tmp_path / "a.npy").write_bytes(b"x" * 100)
    sub = tmp_path / "d"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 23)
    assert _local_size(tmp_path / "a.npy") == 100
    assert _local_size(tmp_path) == 123
    assert _local_size(tmp_path / "missing") is None


def test_plan_size_prefers_binder_value_then_acquisition() -> None:
    acq = Acquisition(protocol="zenodo", resolved_uri="u", landing_local="/x", recursive=False,
                      expected_size=7)
    assert _plan_size(BinderPlan(acquisition=acq)) == 7
    assert _plan_size(BinderPlan(acquisition=acq, size_bytes=9)) == 9
    assert _plan_size(BinderPlan()) is None


class _Array:
    def __init__(self, a):
        from tiled.structures.array import ArrayStructure

        self._s = ArrayStructure.from_array(a)

    def structure(self):
        return self._s


def test_node_size_of_array_from_structure() -> None:
    assert _node_size(_Array(np.zeros((10, 20), dtype="float32")), "array") == 800
    assert _node_size(_Array(np.zeros((3,), dtype="int64")), "array") == 24
    assert _node_size(object(), "table") is None
    assert _node_size(object(), "array") is None  # unreadable structure: unknown, not an error
