"""Start the reference server with the toy incarnation and read it back."""

import json
import math
import sqlite3
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tegel.core.client.data import DataClient
from tegel.core.contracts.resources import StructureFamily
from tegel.core.plugins import catalog_metadata
from tegel.dataserver.app import create_app
from tegel.dataserver.catalog import catalog_has_graph, insert_record
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.spec import load_spec, record_slots
from tegel.dataserver.testing import OIDCIssuer, assert_incarnation
from tegel.toy.dataserver import RECORD, ToyExtractor

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
TOY = EXAMPLES / "toy"
PROFILE = TOY / "schema" / "profile.yaml"
SCHEMA_ROOT = EXAMPLES / "schemas"


@dataclass
class Server:
    http: httpx.Client
    token: str
    catalog: Path
    derived: Path
    blob: Path

    def client(self) -> DataClient:
        return DataClient("http://testserver", token=self.token, client=self.http)

    def get(self, path: str) -> httpx.Response:
        return self.http.get(path, headers={"Authorization": f"Bearer {self.token}"})


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    tmp_path = tmp_path_factory.mktemp("read-api")
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
        calibrated=True,
    )
    _sidecar(
        derived,
        sample_name="red-record",
        uuid="rec-derived",
        recorded="2026-01-02",
        channel="red",
        calibrated=False,
        relations=[{"rel": "derived-from", "target": "rec-raw"}],
    )
    _sidecar(
        blob,
        sample_name="note",
        uuid="rec-bytes",
        recorded="2026-01-03",
        channel="bin",
        calibrated=False,
    )
    plugin = ToyExtractor()
    profile, schema = load_spec(PROFILE, SCHEMA_ROOT)
    slots = record_slots(profile, schema)
    catalog = tmp_path / "catalog.db"
    with OIDCIssuer() as issued:
        config = ServerConfig(
            public_origin="http://test",
            catalog_uri=f"sqlite:///{catalog}",
            profile_path=PROFILE,
            schema_root=SCHEMA_ROOT,
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
            ("rec-bytes", "bytes", blob),
        ):
            insert_record(
                app.state.tegel_catalog,
                key=key,
                metadata=catalog_metadata(
                    plugin,
                    plugin.process(str(path), {}),
                    source_path=path,
                    profile=profile,
                    slots=slots,
                    resource_types={RECORD.name: RECORD},
                ),
                structure_family=family,
            )
        with TestClient(app) as http_client:
            yield Server(http_client, issued.token, catalog, derived, blob)


def test_toy_incarnation_conforms(server: Server) -> None:
    assert_incarnation(
        TOY,
        base_url="http://testserver",
        token=server.token,
        http_client=server.http,
        resource_id="rec-derived",
        search_field="channel",
        search_value="red",
        positive_fixture=server.derived,
        rejected_input=server.blob,
        schema_root=SCHEMA_ROOT,
    )


def test_get_projects_the_envelope(server: Server) -> None:
    with server.client() as client:
        page = client.browse()
        assert page.count == 3
        found = client.get("rec-derived")
        assert found.type == "toy.record"
        assert found.type_version == "1"
        assert found.relations[0].rel == "derived-from"
        assert found.relations[0].target == "rec-raw"
        assert found.provenance is not None
        assert found.provenance.plugin_id == "toy-record"
        assert found.provenance.source_name == "derived.npy"
        assert found.payload_uri == server.derived.resolve().as_uri()
        descriptor = client.open_data("rec-derived")
        assert descriptor.family is StructureFamily.array
        assert descriptor.shape == [4, 5]
    assert server.get("/resources/rec-derived/data/content").status_code == 404


def test_metadata_route_returns_record(server: Server) -> None:
    body = server.get("/resources/rec-derived/metadata").json()
    assert body == {
        "sample_name": "red-record",
        "uuid": "rec-derived",
        "recorded": "2026-01-02",
        "channel": "red",
        "calibrated": False,
    }
    with sqlite3.connect(server.catalog) as connection:
        row = connection.execute(
            "SELECT metadata FROM nodes WHERE key = ?", ("rec-derived",)
        ).fetchone()
    stored = json.loads(row[0])
    assert set(stored) == {"record", "tegel"}
    assert stored["record"] == body


def test_search_by_boolean(server: Server) -> None:
    with server.client() as client:
        calibrated = client.search({"calibrated": "true"})
        assert [hit.uri for hit in calibrated.hits] == ["http://test/resources/rec-raw"]
        assert client.search({"calibrated": "false"}).count == 2
        assert client.search({"calibrated": "True"}).count == 0


def test_content_uses_envelope_payload_uri(server: Server) -> None:
    with server.client() as client:
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
