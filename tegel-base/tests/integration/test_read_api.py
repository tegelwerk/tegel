"""Start the reference server with the toy incarnation and read it back."""

import json
import math
import struct
from pathlib import Path

from fastapi.testclient import TestClient

from tegel.core.client.data import DataClient
from tegel.core.contracts.resources import StructureFamily
from tegel.core.plugins import check_package
from tegel.dataserver.app import create_app
from tegel.dataserver.catalog import catalog_has_graph, insert_record
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.testing import OIDCIssuer, assert_read_only_api
from tegel.toy.dataserver import ToyExtractor

PROFILE = Path(__file__).resolve().parents[2] / "examples" / "toy" / "schema" / "profile.yaml"


def test_search_get_and_open_data(tmp_path: Path) -> None:
    raw = tmp_path / "raw.npy"
    derived = tmp_path / "derived.npy"
    blob = tmp_path / "note.bin"
    _write_npy(raw, (2, 2))
    _write_npy(derived, (4, 5))
    blob.write_bytes(b"payload-bytes")
    _sidecar(
        raw,
        sample_name="raw-record",
        uuid="rec-raw",
        recorded="2026-01-01",
        channel="blue",
    )
    _sidecar(
        derived,
        sample_name="red-record",
        uuid="rec-derived",
        recorded="2026-01-02",
        channel="red",
        relations=[{"rel": "derived-from", "target": "rec-raw"}],
    )
    _sidecar(
        blob,
        sample_name="note",
        uuid="rec-bytes",
        recorded="2026-01-03",
        channel="bin",
    )
    plugin = ToyExtractor()
    catalog = tmp_path / "catalog.db"
    with OIDCIssuer() as issued:
        config = ServerConfig(
            public_origin="http://test",
            catalog_uri=f"sqlite:///{catalog}",
            profile_path=PROFILE,
            issuer_url=issued.issuer,
            audience=issued.audience,
            client_id=issued.client_id,
            client_secret=issued.client_secret,
            session_secret="test-session-secret",
        )
        app = create_app(config)
        assert catalog_has_graph(config.catalog_uri)
        assert "/api/graphql" in _route_paths(app.routes)
        for key, family, path in (
            ("rec-raw", "array", raw),
            ("rec-derived", "array", derived),
        ):
            package = check_package(plugin, plugin.process(str(path), {"key": key}))
            insert_record(
                app.state.tegel_catalog,
                key=key,
                metadata=package.metadata,
                structure_family=family,
            )
        insert_record(
            app.state.tegel_catalog,
            key="rec-bytes",
            metadata={
                "plugin_id": "toy-record",
                "source_name": blob.name,
                "payload_uri": blob.resolve().as_uri(),
                "resource_type": "toy.record",
                "resource_type_version": "1",
                "sample_name": "note",
                "uuid": "rec-bytes",
                "recorded": "2026-01-03",
                "channel": "bin",
                "key": "rec-bytes",
            },
            structure_family="bytes",
        )
        with TestClient(app) as http_client:
            assert_read_only_api(
                "http://testserver",
                token=issued.token,
                http_client=http_client,
                resource_id="rec-derived",
                search_field="channel",
                search_value="red",
            )
            with DataClient(
                "http://testserver",
                token=issued.token,
                client=http_client,
            ) as client:
                page = client.browse()
                assert page.count == 3
                found = client.get("rec-derived")
                assert found.relations[0].rel == "derived-from"
                assert found.relations[0].target == "rec-raw"
                assert found.provenance is not None
                assert found.provenance.plugin_id == "toy-record"
                descriptor = client.open_data("rec-derived")
                assert descriptor.family is StructureFamily.array
                assert descriptor.shape == [4, 5]
                content = http_client.get(
                    "/resources/rec-derived/data/content",
                    headers={"Authorization": f"Bearer {issued.token}"},
                )
                assert content.status_code == 404
                assert client.read_bytes("rec-bytes") == b"payload-bytes"


def _route_paths(routes: object) -> set[str]:
    found: set[str] = set()
    for route in routes:  # type: ignore[union-attr]
        path = getattr(route, "path", None)
        if isinstance(path, str):
            found.add(path)
        context = getattr(route, "include_context", None)
        prefix = getattr(context, "prefix", None)
        if isinstance(prefix, str) and prefix:
            found.add(prefix)
        original = getattr(route, "original_router", None)
        if original is not None:
            found.update(_route_paths(original.routes))
    return found


def _sidecar(path: Path, **fields: object) -> None:
    path.with_suffix(".json").write_text(json.dumps(fields), encoding="utf-8")


def _write_npy(path: Path, shape: tuple[int, ...]) -> None:
    raw = str({"descr": "<f8", "fortran_order": False, "shape": shape}).encode("ascii")
    pad = (64 - ((10 + len(raw) + 1) % 64)) % 64
    header = raw + b" " * pad + b"\n"
    payload = b"\x00" * (8 * math.prod(shape))
    with path.open("wb") as handle:
        handle.write(b"\x93NUMPY\x01\x00")
        handle.write(struct.pack("<H", len(header)))
        handle.write(header)
        handle.write(payload)
