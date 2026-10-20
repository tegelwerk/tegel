"""Pin the Tiled registration behaviour that data parts depend on.

Each format is registered as an externally managed child of a container in a
fresh sqlite catalog, the way Tiled's own ``register_single_item`` builds it
from a URI, then read back through Tiled's HTTP routes.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import pytest
import tifffile
import zarr
from fastapi.testclient import TestClient

KEY = "secret"


def _sources(adapter, mimetype: str, path: Path, is_directory: bool, parameters: dict):
    from tiled.structures.data_source import Asset, DataSource, Management
    from tiled.utils import ensure_uri

    if hasattr(adapter, "generate_data_sources"):
        from tegel.register.tiled._compat import generate_data_sources

        return generate_data_sources(
            adapter,
            mimetype,
            path,
            is_directory,
            lambda s: None if s is None else dataclasses.asdict(s),
            parameters,
        )
    return [
        DataSource(
            structure_family=adapter.structure_family,
            mimetype=mimetype,
            structure=adapter.structure(),
            parameters=parameters,
            management=Management.external,
            assets=[
                Asset(
                    data_uri=ensure_uri(str(path)),
                    is_directory=is_directory,
                    parameter="data_uri",
                )
            ],
        )
    ]


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tiled.catalog import from_uri
    from tiled.config import Authentication
    from tiled.mimetypes import DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE as ADAPTERS
    from tiled.server.app import build_app
    from tiled.structures.core import StructureFamily
    from tiled.utils import ensure_uri

    root = tmp_path_factory.mktemp("spike")
    frames = zarr.create_array(root / "frames.zarr", shape=(4, 3), chunks=(2, 3), dtype="i4")
    frames[:] = np.arange(12).reshape(4, 3)
    with h5py.File(root / "data.h5", "w") as handle:
        handle["entry/data"] = np.arange(6, dtype="f8").reshape(2, 3)
    with h5py.File(root / "master.h5", "w") as handle:
        handle["entry/data"] = h5py.ExternalLink("data.h5", "/entry/data")
    np.save(root / "a.npy", np.arange(5))
    pd.DataFrame({"x": [1, 2], "y": [3.0, 4.0]}).to_parquet(root / "t.parquet")
    (root / "s.csv").write_text("x,y\n1,2\n3,4\n", encoding="utf-8")
    tifffile.imwrite(root / "i.tif", np.arange(16, dtype="u2").reshape(4, 4))

    cases = [
        ("zarr", "application/x-zarr", root / "frames.zarr", True, {}),
        ("h5", "application/x-hdf5", root / "data.h5", False, {"dataset": "entry/data"}),
        ("h5link", "application/x-hdf5", root / "master.h5", False, {"dataset": "entry/data"}),
        ("npy", "application/x-npy", root / "a.npy", False, {}),
        ("csv", "text/csv;header=present", root / "s.csv", False, {}),
        ("tiff", "image/tiff", root / "i.tif", False, {}),
    ]
    if hasattr(ADAPTERS["application/x-parquet"], "from_uris"):  # Tiled 0.2.19 and later
        cases.append(("parquet", "application/x-parquet", root / "t.parquet", False, {}))
    tree = from_uri(
        f"sqlite:///{root / 'catalog.db'}",
        init_if_not_exists=True,
        readable_storage=[str(root)],
    )
    tree.context.streaming_cache = None
    found: dict[str, tuple[str, str]] = {}

    async def register() -> None:
        await tree.create_node(
            structure_family=StructureFamily.container, metadata={}, key="res"
        )
        parent = await tree.lookup_adapter(["res"])
        for name, mimetype, path, is_directory, parameters in cases:
            adapter = ADAPTERS[mimetype].from_uris(ensure_uri(str(path)), **parameters)
            found[name] = (type(adapter).__name__, adapter.structure_family.value)
            await parent.create_node(
                structure_family=adapter.structure_family,
                metadata={},
                key=name,
                data_sources=_sources(adapter, mimetype, path, is_directory, parameters),
            )

    asyncio.run(register())
    app = build_app(tree, authentication=Authentication(single_user_api_key=KEY))
    with TestClient(app, headers={"Authorization": f"Apikey {KEY}"}) as client:
        yield client, found, root


@pytest.mark.parametrize(
    ("name", "adapter", "family"),
    [
        ("zarr", "ZarrArrayAdapter", "array"),
        ("h5", "HDF5ArrayAdapter", "array"),
        ("h5link", "HDF5ArrayAdapter", "array"),
        ("npy", "NPYAdapter", "array"),
        ("csv", "CSVAdapter", "table"),
        ("tiff", "TiffAdapter", "array"),
    ],
)
def test_format_registers_as_external_child(served, name, adapter, family) -> None:
    client, found, _ = served
    assert found[name] == (adapter, family)
    body = client.get(
        f"/api/v1/metadata/res/{name}", params={"include_data_sources": True}
    ).json()
    attributes = body["data"]["attributes"]
    assert attributes["structure_family"] == family
    assert attributes["data_sources"][0]["management"] == "external"


def test_array_slices_through_tiled_routes(served) -> None:
    client, _, _ = served
    json_format = {"format": "application/json"}
    zarr_slice = client.get("/api/v1/array/full/res/zarr", params={"slice": "0:1", **json_format})
    assert zarr_slice.json() == [[0, 1, 2]]
    assert client.get(
        "/api/v1/array/full/res/h5", params={"slice": "1", **json_format}
    ).json() == [3.0, 4.0, 5.0]
    assert client.get(
        "/api/v1/array/full/res/npy", params={"slice": "1:3", **json_format}
    ).json() == [1, 2]
    assert client.get(
        "/api/v1/array/full/res/tiff", params={"slice": "0", **json_format}
    ).json() == [0, 1, 2, 3]
    block = client.get(
        "/api/v1/array/block/res/zarr", params={"block": "1,0", **json_format}
    )
    assert block.json() == [[6, 7, 8], [9, 10, 11]]


def test_hdf5_external_link_resolves(served) -> None:
    client, _, _ = served
    response = client.get("/api/v1/array/full/res/h5link", params={"format": "application/json"})
    assert response.json() == [[0.0, 1.0, 2.0], [3.0, 4.0, 5.0]]


def test_table_column_through_tiled_routes(served) -> None:
    client, _, _ = served
    response = client.get(
        "/api/v1/table/full/res/csv", params={"column": "x", "format": "text/csv"}
    )
    assert response.status_code == 200
    assert response.text.split() == ["x", "1", "3"]
    partition = client.get(
        "/api/v1/table/partition/res/csv", params={"partition": 0, "format": "text/csv"}
    )
    assert partition.status_code == 200


def test_metadata_body_shape(served) -> None:
    client, _, _ = served
    attributes = client.get("/api/v1/metadata/res/zarr").json()["data"]["attributes"]
    assert attributes["structure"]["shape"] == [4, 3]
    table = client.get("/api/v1/metadata/res/csv").json()["data"]["attributes"]["structure"]
    assert table["columns"] == ["x", "y"]


def test_parquet_registers_from_a_uri_only_from_tiled_0_2_19(served) -> None:
    """Tiled 0.2.18 has no URI registration for Parquet, so staging serves it as bytes.

    From 0.2.19 ``tiled_registers`` sees ``from_uris`` and the part becomes a table node.
    """
    import tiled
    from tiled.mimetypes import DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE as ADAPTERS

    from tegel.actions.parts import tiled_registers

    client, found, _ = served
    newer = tuple(int(p) for p in tiled.__version__.split(".")[:3]) >= (0, 2, 19)
    assert hasattr(ADAPTERS["application/x-parquet"], "from_uris") is newer
    assert tiled_registers("application/x-parquet") is newer
    if not newer:
        return
    assert found["parquet"][1] == "table"
    response = client.get(
        "/api/v1/table/full/res/parquet", params={"column": "y", "format": "text/csv"}
    )
    assert response.status_code == 200
    assert response.text.split() == ["y", "3.0", "4.0"]


def test_registration_outside_readable_storage_fails_on_read(tmp_path: Path) -> None:
    from tiled.catalog import from_uri
    from tiled.config import Authentication
    from tiled.mimetypes import DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE as ADAPTERS
    from tiled.server.app import build_app
    from tiled.structures.core import StructureFamily
    from tiled.utils import ensure_uri

    data = tmp_path / "outside"
    data.mkdir()
    np.save(data / "a.npy", np.arange(3))
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    tree = from_uri(
        f"sqlite:///{tmp_path / 'catalog.db'}",
        init_if_not_exists=True,
        readable_storage=[str(allowed)],
    )
    tree.context.streaming_cache = None
    adapter = ADAPTERS["application/x-npy"].from_uris(ensure_uri(str(data / "a.npy")))

    async def register() -> None:
        await tree.create_node(
            structure_family=StructureFamily.array,
            metadata={},
            key="a",
            data_sources=_sources(adapter, "application/x-npy", data / "a.npy", False, {}),
        )

    asyncio.run(register())
    app = build_app(tree, authentication=Authentication(single_user_api_key=KEY))
    with TestClient(
        app, headers={"Authorization": f"Apikey {KEY}"}, raise_server_exceptions=False
    ) as client:
        response = client.get("/api/v1/array/full/a", params={"format": "application/json"})
    assert response.status_code >= 400
