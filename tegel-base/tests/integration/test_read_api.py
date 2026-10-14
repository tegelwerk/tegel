"""Stage and merge toy records, serve them, and read every kind of part back."""

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tegel.actions.merge import merge_staging
from tegel.actions.staging import write_staging
from tegel.core.client.data import DataClient
from tegel.dataserver.app import create_app
from tegel.dataserver.catalog import catalog_has_graph
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.testing import OIDCIssuer, assert_incarnation
from tegel.toy.dataserver.fixtures import NOTES, STATS, write_record

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
TOY = EXAMPLES / "toy"
PROFILE = TOY / "schema" / "profile.yaml"
SCHEMA_ROOT = EXAMPLES / "schemas"
ORIGIN = "http://testserver"


@dataclass
class Server:
    http: httpx.Client
    token: str
    catalog: Path
    records: Path
    issued: OIDCIssuer

    def client(self) -> DataClient:
        return DataClient(ORIGIN, token=self.token, client=self.http)

    def get(self, path: str, **headers: str) -> httpx.Response:
        sent = {"Authorization": f"Bearer {self.token}", **headers}
        return self.http.get(path, headers=sent)


def config_for(catalog: Path, records: Path, issued, origin: str = ORIGIN) -> ServerConfig:
    return ServerConfig(
        public_origin=origin,
        catalog_uri=f"sqlite:///{catalog}",
        profile_path=PROFILE,
        schema_root=SCHEMA_ROOT,
        issuer_url=issued.issuer,
        audience=issued.audience,
        client_id=issued.client_id,
        client_secret=issued.client_secret,
        session_secret="test-session-secret",
        readable_storage=[records],
    )


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    tmp_path = tmp_path_factory.mktemp("read-api")
    records = tmp_path / "records"
    raw = write_record(
        records / "raw",
        {
            "sample_name": "raw-record",
            "uuid": "rec-raw",
            "recorded": "2026-01-01",
            "channel": "blue",
            "calibrated": True,
        },
        stats=False,
        notes=False,
    )
    derived = write_record(
        records / "derived",
        {
            "sample_name": "red-record",
            "uuid": "rec-derived",
            "recorded": "2026-01-02",
            "channel": "red",
            "calibrated": False,
            "relations": [{"rel": "derived-from", "target": "rec-raw"}],
        },
    )
    note = write_record(
        records / "note",
        {
            "sample_name": "note",
            "uuid": "rec-bytes",
            "recorded": "2026-01-03",
            "channel": "bin",
            "calibrated": False,
        },
        frames=False,
        stats=False,
    )
    staging = tmp_path / "staging.db"
    catalog = tmp_path / "catalog.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TEGEL_PROFILE", str(PROFILE))
        patch.setenv("TEGEL_SCHEMA_ROOT", str(SCHEMA_ROOT))
        result = write_staging([str(raw), str(derived), str(note)], staging, roots=[records])
    assert (result.staged, result.skipped) == (3, 0), result.errors
    merge_staging(str(staging), str(catalog))
    with OIDCIssuer() as issued:
        app = create_app(config_for(catalog, records, issued))
        assert catalog_has_graph(f"sqlite:///{catalog}")
        assert "/api/graphql" in _route_paths(app.routes)
        with TestClient(app) as http_client:
            yield Server(http_client, issued.token, catalog, records, issued)


def test_toy_incarnation_conforms(server: Server) -> None:
    assert_incarnation(
        TOY,
        base_url=ORIGIN,
        token=server.token,
        http_client=server.http,
        resource_id="rec-derived",
        search_field="channel",
        search_value="red",
        positive_fixture=server.records / "derived" / "record.json",
        rejected_input=server.records / "derived" / "notes.txt",
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
        assert found.provenance.source_name == "record.json"
        marker = server.records / "derived" / "record.json"
        assert found.payload_uri == marker.resolve().as_uri()
        assert [part.name for part in found.parts] == ["frames", "stats", "notes"]
        assert all(part.content_url is None for part in found.parts)
    body = server.get("/resources/rec-derived").json()
    assert all("content_url" not in part for part in body["parts"])


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
            "SELECT metadata FROM nodes WHERE key = ? AND parent = 0", ("rec-derived",)
        ).fetchone()
    stored = json.loads(row[0])
    assert set(stored) == {"record", "tegel"}
    assert stored["record"] == body
    assert [part["served_by"] for part in stored["tegel"]["parts"]] == ["tiled", "tiled", "tegel"]


def test_search_by_boolean(server: Server) -> None:
    with server.client() as client:
        calibrated = client.search({"calibrated": "true"})
        assert [hit.uri for hit in calibrated.hits] == [f"{ORIGIN}/resources/rec-raw"]
        assert client.search({"calibrated": "false"}).count == 2
        assert client.search({"calibrated": "True"}).count == 0


def test_data_lists_parts_with_urls(server: Server, tmp_path: Path) -> None:
    body = server.get("/resources/rec-derived/data").json()
    urls = {part["name"]: part["content_url"] for part in body["parts"]}
    assert urls == {
        "frames": f"{ORIGIN}/api/v1/array/full/rec-derived/frames",
        "stats": f"{ORIGIN}/api/v1/table/full/rec-derived/stats",
        "notes": f"{ORIGIN}/resources/rec-derived/data/notes/content",
    }
    assert body["payload"]["content_url"] == f"{ORIGIN}/resources/rec-derived/data/content"
    assert body["payload"]["media_type"] == "application/json"
    rooted = create_app(
        config_for(server.catalog, server.records, server.issued, "https://data.example/tegel")
    )
    with TestClient(rooted) as client:
        headers = {"Authorization": f"Bearer {server.token}"}
        rooted_body = client.get("/resources/rec-derived/data", headers=headers).json()
    rooted_urls = {part["name"]: part["content_url"] for part in rooted_body["parts"]}
    assert rooted_urls["frames"] == "https://data.example/tegel/api/v1/array/full/rec-derived/frames"
    assert rooted_urls["notes"] == (
        "https://data.example/tegel/resources/rec-derived/data/notes/content"
    )


def test_array_slice_through_content_url(server: Server) -> None:
    with server.client() as client:
        found = client.read_array("rec-derived", "frames", slice="0:1")
    assert (found.shape, found.data_type) == ([4, 3], "<i4")
    assert np.frombuffer(found.data, dtype=found.data_type).tolist() == [0, 1, 2]
    url = f"{ORIGIN}/api/v1/array/full/rec-derived/frames"
    as_json = server.get(f"{url}?slice=1:3,1&format=application/json").json()
    assert as_json == [4, 7]


def test_table_columns_through_content_url(server: Server) -> None:
    with server.client() as client:
        column = client.read_table("rec-derived", "stats", columns=["y"])
        whole = client.read_table("rec-derived", "stats", partition=0)
    assert column.decode().split() == ["y", "2.5", "4.5"]
    assert whole.decode().split() == STATS.split()


def test_text_range_206(server: Server) -> None:
    response = server.get("/resources/rec-derived/data/notes/content", Range="bytes=2-5")
    assert response.status_code == 206
    assert response.content == NOTES.encode()[2:6]
    assert response.headers["content-range"] == f"bytes 2-5/{len(NOTES)}"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-type"].startswith("text/plain")
    full = server.get("/resources/rec-derived/data/notes/content")
    assert full.status_code == 200
    assert full.content == NOTES.encode()
    assert full.headers["accept-ranges"] == "bytes"
    with server.client() as client:
        assert client.read_bytes("rec-derived", "notes", start=0, end=3) == b"toy "


def test_text_suffix_range(server: Server) -> None:
    response = server.get("/resources/rec-derived/data/notes/content", Range="bytes=-3")
    assert response.status_code == 206
    assert response.content == NOTES.encode()[-3:]
    size = len(NOTES)
    assert response.headers["content-range"] == f"bytes {size - 3}-{size - 1}/{size}"
    tail = server.get("/resources/rec-derived/data/notes/content", Range="bytes=10-")
    assert tail.content == NOTES.encode()[10:]


def test_range_416(server: Server) -> None:
    size = len(NOTES)
    for header in (f"bytes={size}-", "bytes=0-1,4-5", "bytes=5-2", "bytes=-0"):
        response = server.get("/resources/rec-derived/data/notes/content", Range=header)
        assert response.status_code == 416, header
        assert response.headers["content-range"] == f"bytes */{size}"


def test_payload_range(server: Server) -> None:
    marker = (server.records / "note" / "record.json").read_bytes()
    response = server.get("/resources/rec-bytes/data/content", Range="bytes=0-0")
    assert response.status_code == 206
    assert response.content == b"{"
    assert response.headers["content-type"].startswith("application/json")
    with server.client() as client:
        assert client.read_bytes("rec-bytes") == marker
        assert client.read_bytes("rec-bytes", start=1, end=4) == marker[1:5]


def test_tiled_part_on_tegel_route_404(server: Server) -> None:
    response = server.get("/resources/rec-derived/data/frames/content")
    assert response.status_code == 404
    assert response.json() == {"detail": "Part is served by Tiled; use content_url"}
    missing = server.get("/resources/rec-derived/data/nope/content")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Part not found"}


def test_tiled_routes_need_token(server: Server) -> None:
    url = f"{ORIGIN}/api/v1/array/full/rec-derived/frames"
    assert server.http.get(url).status_code == 401
    assert server.http.get(f"{ORIGIN}/resources/rec-derived/data/notes/content").status_code == 401
    assert server.get(url).status_code == 200


def test_write_routes_stay_closed(server: Server) -> None:
    url = f"{ORIGIN}/api/v1/array/full/rec-derived/frames"
    headers = {"Authorization": f"Bearer {server.token}"}
    written = server.http.put(url, headers=headers, content=b"\x00" * 48)
    assert written.status_code == 401
    assert "write:data" in written.json()["detail"]
    delete = server.http.delete(f"{ORIGIN}/api/v1/metadata/rec-derived/frames", headers=headers)
    assert delete.status_code == 401, delete.text
    post = server.http.post(
        f"{ORIGIN}/resources/rec-derived/data/notes/content",
        headers={"Authorization": f"Bearer {server.token}"},
    )
    assert post.status_code == 405


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
