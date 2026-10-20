"""File REFERENCE registration against an in-process Tiled server."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

pytest.importorskip("tiled")

import numpy as np

from tegel.register import register
from tegel.register.bind import plan
from tegel.register.context import BindContext
from tegel.register.errors import BindError, KeyConflictError, ModeNotAvailableError


def _context(site):
    return BindContext(site=site)


def _family(node) -> str:
    family = node.item["attributes"]["structure_family"]
    return family.value if hasattr(family, "value") else str(family)


def _assert_assets_readable(node, srvview: Path) -> None:
    root = str(srvview)
    if _family(node) == "container":
        for key in list(node):
            _assert_assets_readable(node[key], srvview)
        return
    sources = node.data_sources()
    if not sources:
        return
    for source in sources:
        for asset in source.assets:
            uri = str(asset.data_uri)
            if not uri.startswith("file:"):
                continue
            path = url2pathname(urlparse(uri).path)
            assert os.path.commonpath([root, path]) == root, uri


@pytest.fixture
def checked(tiled_site):
    client, site, real, srvview, _server = tiled_site
    yield client, site, real, srvview
    _assert_assets_readable(client, srvview)


def test_reference_npy_csv_tiff_h5(checked):
    client, site, real, srvview = checked
    npy = real / "frame.npy"
    array = np.arange(6, dtype=np.float32).reshape(2, 3)
    np.save(npy, array)
    csv_path = real / "rows.csv"
    csv_path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    tiff = real / "image.tiff"
    tifffile = pytest.importorskip("tifffile")
    image = np.arange(12, dtype=np.uint16).reshape(3, 4)
    tifffile.imwrite(tiff, image)
    h5py = pytest.importorskip("h5py")
    hdf = real / "meas.h5"
    with h5py.File(hdf, "w") as handle:
        handle["data"] = np.arange(4, dtype=np.int32)

    ctx = _context(site)
    for path, reader in (
        (npy, lambda node: np.asarray(node.read())),
        (csv_path, lambda node: list(node.read()["a"])),
        (tiff, lambda node: np.asarray(node.read())),
        (hdf, lambda node: np.asarray(node["data"].read())),
    ):
        binding = register(path.as_uri(), client, context=ctx)
        assert binding.created is True
        assert binding.origin.mode.value == "reference"
        assert binding.node.metadata["tegel_register"]["mode"] == "reference"
        specs = binding.node.item["attributes"]["specs"]
        assert any(spec.get("name") == "tegel-register-origin" for spec in specs)
        if _family(binding.node) != "container":
            uris = [asset.data_uri for source in binding.node.data_sources() for asset in source.assets]
            assert uris
            assert all(str(srvview) in uri for uri in uris)
            assert str(real) not in uris[0]
        reader(binding.node)


def test_outside_readable_reference_is_unavailable(checked, tmp_path):
    client, site, _real, _srvview = checked
    outside = tmp_path / "outside.npy"
    np.save(outside, np.arange(2))
    with pytest.raises(ModeNotAvailableError, match="not under any readable entry") as raised:
        plan(outside.as_uri(), client, mode="reference", context=_context(site))
    assert raised.value.reasons
    with pytest.raises(ModeNotAvailableError, match="not under any readable entry"):
        plan(outside.as_uri(), client, mode="auto", context=_context(site))


def test_directory_recursive_and_notes(checked):
    client, site, real, _srvview = checked
    folder = real / "run"
    folder.mkdir()
    np.save(folder / "a.npy", np.arange(3))
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    ctx = _context(site)
    with pytest.raises(BindError, match="directory needs \\?recursive"):
        plan(folder.as_uri(), client, context=ctx)
    planned = plan(folder.as_uri() + "?recursive", client, context=ctx)
    assert planned.node is not None
    assert planned.moves_bytes is False  # a reference reads in place
    assert planned.size_bytes == (folder / "a.npy").stat().st_size + len("hello")
    assert planned.node.structure_family == "container"
    names = {child.key for child in planned.node.children}
    assert "a" in names
    assert any("notes.txt" in note and "undescribable" in note for note in planned.notes)
    binding = register(folder.as_uri() + "?recursive", client, context=ctx)
    assert _family(binding.node) == "container"
    assert np.array_equal(np.asarray(binding.node["a"].read()), np.arange(3))


def test_idempotent_register_and_key_conflict(checked):
    client, site, real, _srvview = checked
    first = real / "one.npy"
    second = real / "two.npy"
    np.save(first, np.array([1, 2, 3]))
    np.save(second, np.array([4, 5, 6]))
    ctx = _context(site)
    created = register(first.as_uri(), client, context=ctx)
    again = register(first.as_uri(), client, context=ctx)
    assert again.created is False
    assert again.path == created.path
    with pytest.raises(KeyConflictError, match="one"):
        register(second.as_uri(), client, key="one", context=ctx)
    replaced = register(second.as_uri(), client, key="one", on_conflict="replace", context=ctx)
    assert replaced.created is True
    assert np.array_equal(np.asarray(replaced.node.read()), np.array([4, 5, 6]))


def test_plan_does_not_write(checked):
    client, site, real, _srvview = checked
    path = real / "quiet.npy"
    np.save(path, np.arange(2))
    before = sorted(client)
    listing = sorted(p.name for p in real.iterdir())
    plan(path.as_uri(), client, context=_context(site))
    assert sorted(client) == before
    assert sorted(p.name for p in real.iterdir()) == listing


def test_readme_reference_example(checked):
    client, site, real, _srvview = checked
    frame = real / "frame.h5"
    h5py = pytest.importorskip("h5py")
    with h5py.File(frame, "w") as handle:
        handle["data"] = np.arange(4)
    binding = register(frame.as_uri(), into=client, context=_context(site))
    assert binding.origin.mode.value == "reference"
    assert binding.path.endswith("/frame")
