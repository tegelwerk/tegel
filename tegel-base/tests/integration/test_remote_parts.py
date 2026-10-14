"""Remote parts: link and proxy parts on an upstream Tiled server (R2)."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tegel.actions.merge import merge_staging
from tegel.actions.staging import write_staging
from tegel.core.client.data import DataClient
from tegel.core.contracts.resources import DataDescriptor, DataPart, StructureFamily
from tegel.core.upstreams import Upstream
from tegel.dataserver.app import create_app
from tegel.dataserver.config import ServerConfig
from tegel.dataserver.proxy import UpstreamClients
from tegel.dataserver.testing import OIDCIssuer
from tegel.toy.dataserver import ToyReader
from tegel.toy.dataserver.fixtures import write_record

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PROFILE = EXAMPLES / "toy" / "schema" / "profile.yaml"
SCHEMA_ROOT = EXAMPLES / "schemas"
ORIGIN = "http://testserver"
UPSTREAM = "https://beamline.example"
KEY = "beamlinesecret"
METADATA = f"{UPSTREAM}/api/v1/metadata/rec-up"
RECORD = {"sample_name": "remote", "recorded": "2026-02-01", "channel": "far", "calibrated": True}
API_KEY_UPSTREAM = Upstream(origin=UPSTREAM, auth="api_key", api_key_env="BEAMLINE_KEY")


def _remote(name: str, family: StructureFamily, remote: str, path: str) -> DataPart:
    return DataPart(
        name=name,
        family=family,
        media_type="application/x-tiled",
        asset_uri=f"{METADATA}/{path}",
        remote=remote,
    )


REMOTE_PARTS = [
    _remote("frames_link", StructureFamily.array, "link", "frames"),
    _remote("frames_proxy", StructureFamily.array, "proxy", "frames"),
    _remote("stats_proxy", StructureFamily.table, "proxy", "stats"),
]


def _with_remote(parts: list[DataPart]):
    original = ToyReader.describe

    def describe(self, resource):
        found = original(self, resource)
        if resource.id != "rec-remote":
            return found
        return DataDescriptor(parts=[*found.parts, *parts], payload=found.payload)

    return describe


class _Streamed(httpx.SyncByteStream):
    """A body that is streamed like a network response, not preloaded."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __iter__(self):
        yield self.body


def _streamed(body: bytes, **headers: str) -> httpx.Response:
    return httpx.Response(200, stream=_Streamed(body), headers=headers)


def _lines(path: Path) -> list[dict]:
    return [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines()]


@dataclass
class Remote:
    tegel: TestClient
    upstream: TestClient
    token: str
    records: Path
    catalog: Path
    issued: OIDCIssuer
    upstreams_file: Path

    def get(self, path: str, **params) -> httpx.Response:
        return self.tegel.get(
            path, params=params, headers={"Authorization": f"Bearer {self.token}"}
        )


@pytest.fixture(scope="module")
def remote(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Remote]:
    from tiled.catalog import from_uri
    from tiled.config import Authentication
    from tiled.server.app import build_app

    tmp_path = tmp_path_factory.mktemp("remote")
    records = tmp_path / "records"
    upstreams_file = tmp_path / "upstreams.yaml"
    upstreams_file.write_text(
        f"upstreams:\n  - origin: {UPSTREAM}\n    auth: api_key\n    api_key_env: BEAMLINE_KEY\n",
        encoding="utf-8",
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TEGEL_PROFILE", str(PROFILE))
        patch.setenv("TEGEL_SCHEMA_ROOT", str(SCHEMA_ROOT))
        patch.setenv("BEAMLINE_KEY", KEY)
        patch.setenv("TEGEL_UPSTREAMS", str(upstreams_file))
        up_marker = write_record(records / "up", {**RECORD, "uuid": "rec-up"}, notes=False)
        upstream_db = tmp_path / "upstream.db"
        write_staging([str(up_marker)], tmp_path / "up-staging.db", roots=[records])
        merge_staging(str(tmp_path / "up-staging.db"), str(upstream_db))
        tree = from_uri(f"sqlite:///{upstream_db}", readable_storage=[str(records)])
        upstream_app = build_app(tree, authentication=Authentication(single_user_api_key=KEY))
        with TestClient(upstream_app, base_url=UPSTREAM) as upstream:
            patch.setattr(ToyReader, "describe", _with_remote(REMOTE_PARTS))
            marker = write_record(
                records / "remote",
                {**RECORD, "uuid": "rec-remote"},
                frames=False,
                stats=False,
            )
            staging = tmp_path / "staging.db"
            result = write_staging(
                [str(marker)], staging, roots=[records], upstream_client=upstream
            )
            assert result.skipped == 0, result.errors
            catalog = tmp_path / "catalog.db"
            merge_staging(str(staging), str(catalog))
            with OIDCIssuer() as issued:
                app = create_app(_config(catalog, records, issued, upstreams_file))
                app.state.tegel_upstreams.transport = upstream._transport
                with TestClient(app) as tegel:
                    yield Remote(
                        tegel, upstream, issued.token, records, catalog, issued, upstreams_file
                    )


def _config(catalog: Path, records: Path, issued, upstreams_file: Path) -> ServerConfig:
    return ServerConfig(
        public_origin=ORIGIN,
        catalog_uri=f"sqlite:///{catalog}",
        profile_path=PROFILE,
        schema_root=SCHEMA_ROOT,
        issuer_url=issued.issuer,
        audience=issued.audience,
        client_id=issued.client_id,
        client_secret=issued.client_secret,
        session_secret="test-session-secret",
        readable_storage=[records],
        upstreams_path=upstreams_file,
    )


def _mocked(remote: Remote, handler, upstream: Upstream = API_KEY_UPSTREAM) -> TestClient:
    app = create_app(_config(remote.catalog, remote.records, remote.issued, remote.upstreams_file))
    app.state.tegel_upstreams = UpstreamClients(
        {upstream.origin: upstream}, timeout=5, transport=httpx.MockTransport(handler)
    )
    return TestClient(app)


def _stage_one(tmp_path: Path, monkeypatch, parts: list[DataPart], client) -> tuple:
    monkeypatch.setenv("TEGEL_PROFILE", str(PROFILE))
    monkeypatch.setenv("BEAMLINE_KEY", KEY)
    monkeypatch.setattr(ToyReader, "describe", _with_remote(parts))
    marker = write_record(
        tmp_path / "remote", {**RECORD, "uuid": "rec-remote"}, frames=False, stats=False
    )
    other = write_record(tmp_path / "other", {**RECORD, "uuid": "rec-other"}, frames=False)
    log = tmp_path / "errors.jsonl"
    result = write_staging(
        [str(marker), str(other)],
        tmp_path / "staging.db",
        error_log=log,
        roots=[tmp_path],
        upstreams={UPSTREAM: API_KEY_UPSTREAM},
        upstream_client=client,
    )
    return result, _lines(log)


def _metadata_handler(family: str, structure: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"data": {"attributes": {"structure_family": family, "structure": structure}}}
        return httpx.Response(200, json=body)

    return handler


def test_unlisted_upstream_is_parts_error(tmp_path: Path, monkeypatch) -> None:
    unlisted = DataPart(
        name="far",
        family=StructureFamily.array,
        media_type="application/x-tiled",
        asset_uri="https://elsewhere.example/api/v1/metadata/x",
        remote="link",
    )
    client = httpx.Client(transport=httpx.MockTransport(_metadata_handler("array", {})))
    result, lines = _stage_one(tmp_path, monkeypatch, [unlisted], client)
    assert (result.staged, result.skipped) == (1, 1)
    [line] = lines
    assert line["stage"] == "parts"
    assert "https://elsewhere.example/api/v1/metadata/x" in line["message"]
    assert "not listed in TEGEL_UPSTREAMS" in line["message"]


def test_remote_family_mismatch_is_parts_error(tmp_path: Path, monkeypatch) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(_metadata_handler("table", {"columns": ["x"]}))
    )
    result, lines = _stage_one(tmp_path, monkeypatch, [REMOTE_PARTS[0]], client)
    assert result.skipped == 1
    [line] = lines
    assert line["stage"] == "parts"
    assert "upstream node is table, the part declares array" in line["message"]


def test_upstream_unreachable_is_parts_error(tmp_path: Path, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result, lines = _stage_one(tmp_path, monkeypatch, [REMOTE_PARTS[1]], client)
    assert (result.staged, result.skipped) == (1, 1)
    [line] = lines
    assert line["stage"] == "parts"
    assert f"upstream {UPSTREAM} timed out" in line["message"]


def test_remote_parts_are_stored_with_their_upstream(remote: Remote) -> None:
    found = {part["name"]: part for part in remote.get("/resources/rec-remote").json()["parts"]}
    assert found["frames_link"]["served_by"] == "link"
    assert found["frames_link"]["upstream"] == UPSTREAM
    assert found["frames_link"]["remote_path"] == "rec-up/frames"
    assert found["frames_link"]["shape"] == [4, 3]
    assert found["frames_link"]["data_type"] == "<i4"
    assert found["stats_proxy"]["columns"] == ["x", "y"]
    assert found["notes"]["served_by"] == "tegel"


def test_link_content_url_points_upstream(remote: Remote) -> None:
    body = remote.get("/resources/rec-remote/data").json()
    urls = {part["name"]: part["content_url"] for part in body["parts"]}
    assert urls["frames_link"] == f"{UPSTREAM}/api/v1/array/full/rec-up/frames"
    assert urls["frames_proxy"] == f"{ORIGIN}/resources/rec-remote/data/frames_proxy/content"
    assert urls["stats_proxy"] == f"{ORIGIN}/resources/rec-remote/data/stats_proxy/content"
    response = remote.get("/resources/rec-remote/data/frames_link/content")
    assert response.status_code == 404
    assert response.json() == {"detail": "Part is served by its upstream; use content_url"}


def test_proxy_array_slice_streams(remote: Remote) -> None:
    params = {"slice": "0:1", "format": "application/octet-stream"}
    through = remote.get("/resources/rec-remote/data/frames_proxy/content", **params)
    direct = remote.upstream.get(
        "/api/v1/array/full/rec-up/frames",
        params=params,
        headers={"Authorization": f"Apikey {KEY}"},
    )
    assert through.status_code == 200
    assert through.content == direct.content
    assert len(through.content) == 3 * 4
    assert through.headers["content-type"] == direct.headers["content-type"]
    block = remote.get(
        "/resources/rec-remote/data/frames_proxy/content", block="1,0", format="application/json"
    )
    assert block.json() == [[6, 7, 8], [9, 10, 11]]
    with DataClient(ORIGIN, token=remote.token, client=remote.tegel) as client:
        found = client.read_array("rec-remote", "frames_proxy", slice="1")
    assert found.data == bytes([3, 0, 0, 0, 4, 0, 0, 0, 5, 0, 0, 0])


def test_proxy_table_column_and_partition(remote: Remote) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _streamed(b"y\n2.5\n", **{"content-type": "text/csv"})

    with _mocked(remote, handler) as client:
        headers = {"Authorization": f"Bearer {remote.token}"}
        path = "/resources/rec-remote/data/stats_proxy/content"
        assert client.get(path, params={"column": "y", "format": "text/csv"}, headers=headers)
        client.get(path, params={"partition": "0"}, headers=headers)
    assert seen[0].url.path == "/api/v1/table/full/rec-up/stats"
    assert seen[0].url.params.get_list("column") == ["y"]
    assert seen[1].url.path == "/api/v1/table/partition/rec-up/stats"
    assert seen[1].url.params["partition"] == "0"
    column = remote.get(
        "/resources/rec-remote/data/stats_proxy/content", column="y", format="text/csv"
    )
    assert column.text.split() == ["y", "2.5", "4.5"]


def test_proxy_uses_upstream_credentials(remote: Remote, monkeypatch) -> None:
    monkeypatch.setenv("BEAMLINE_KEY", KEY)
    monkeypatch.setenv("BEAMLINE_SECRET", "client-secret")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/token":
            assert b"client_secret=client-secret" in request.read()
            return httpx.Response(200, json={"access_token": "minted", "expires_in": 300})
        return _streamed(b"ok")

    path = "/resources/rec-remote/data/frames_proxy/content"
    caller = {"Authorization": f"Bearer {remote.token}"}
    with _mocked(remote, handler) as client:
        assert client.get(path, headers=caller).status_code == 200
    assert seen[-1].headers["authorization"] == f"Apikey {KEY}"
    credentials = Upstream(
        origin=UPSTREAM,
        auth="client_credentials",
        token_url=f"{UPSTREAM}/token",
        client_id="tegel",
        client_secret_env="BEAMLINE_SECRET",
    )
    seen.clear()
    with _mocked(remote, handler, credentials) as client:
        client.get(path, headers=caller)
        client.get(path, headers=caller)
    assert [request.url.path for request in seen].count("/token") == 1
    data_requests = [request for request in seen if request.url.path != "/token"]
    assert [request.headers["authorization"] for request in data_requests] == ["Bearer minted"] * 2
    assert all(remote.token not in str(request.headers) for request in seen)


def test_proxy_drops_unlisted_params_and_methods(remote: Remote) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _streamed(b"ok")

    path = "/resources/rec-remote/data/frames_proxy/content"
    headers = {"Authorization": f"Bearer {remote.token}", "Range": "bytes=0-1", "X-Other": "1"}
    with _mocked(remote, handler) as client:
        params = {"slice": "0", "evil": "x", "url": "https://attacker.example"}
        assert client.get(path, params=params, headers=headers).status_code == 200
        assert client.post(path, headers=headers).status_code == 405
    [request] = seen
    assert dict(request.url.params) == {"slice": "0"}
    assert request.url.host == "beamline.example"
    assert request.headers["range"] == "bytes=0-1"
    assert "x-other" not in request.headers


def test_proxy_upstream_errors(remote: Remote) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params.get("slice")
        if mode == "1":
            return httpx.Response(401, json={"detail": "no"})
        if mode == "2":
            raise httpx.ReadTimeout("slow", request=request)
        if mode == "3":
            return httpx.Response(404, json={"detail": "gone"})
        if mode == "4":
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(500, text="broken")

    path = "/resources/rec-remote/data/frames_proxy/content"
    headers = {"Authorization": f"Bearer {remote.token}"}
    with _mocked(remote, handler) as client:
        refused = client.get(path, params={"slice": "1"}, headers=headers)
        timeout = client.get(path, params={"slice": "2"}, headers=headers)
        missing = client.get(path, params={"slice": "3"}, headers=headers)
        down = client.get(path, params={"slice": "4"}, headers=headers)
        broken = client.get(path, params={"slice": "5"}, headers=headers)
    assert (refused.status_code, refused.json()) == (
        502,
        {"detail": "Upstream refused Tegel's credentials"},
    )
    assert (timeout.status_code, timeout.json()) == (
        504,
        {"detail": f"Upstream {UPSTREAM} failed: timed out"},
    )
    assert missing.status_code == 404
    assert down.status_code == 502
    assert down.json()["detail"].startswith(f"Upstream {UPSTREAM} failed: ")
    assert (broken.status_code, broken.json()) == (
        502,
        {"detail": f"Upstream {UPSTREAM} failed: HTTP 500"},
    )
