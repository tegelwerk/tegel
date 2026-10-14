"""DataClient against an in-memory HTTP transport."""

import json

import httpx
import pytest

from tegel.core.client.data import DataClient, DataClientError
from tegel.core.contracts.resources import SearchResult, build_search_crate, hit_from_metadata
from tegel.core.contracts.types import SearchProfile
from tegel.core.version import SPEC_VERSION

PROFILE = SearchProfile.model_validate(
    {
        "id": "https://example.com/profile",
        "name": "Sample search",
        "schema_id": "https://example.com/sample",
        "schema_version": "1",
        "schema_path": "sample.yaml",
        "class": "Sample",
        "search": ["kind"],
        "properties": ["kind"],
        "entity": {"name": "sample_name", "identifier": "uuid", "dateCreated": "recorded"},
    }
)


FRAMES = {
    "name": "frames",
    "family": "array",
    "media_type": "application/x-zarr",
    "asset_uri": "file:///records/rec/frames.zarr",
    "designated": True,
    "shape": [4, 3],
    "data_type": "<i4",
    "served_by": "tiled",
    "content_url": "https://data.example/api/v1/array/full/rec/frames",
}
STATS = {
    "name": "stats",
    "family": "table",
    "media_type": "text/csv;header=present",
    "asset_uri": "file:///records/rec/stats.csv",
    "columns": ["x", "y"],
    "served_by": "tiled",
    "content_url": "https://data.example/api/v1/table/full/rec/stats",
}
NOTES = {
    "name": "notes",
    "family": "bytes",
    "media_type": "text/plain",
    "asset_uri": "file:///records/rec/notes.txt",
    "served_by": "tegel",
    "content_url": "https://data.example/resources/rec/data/notes/content",
}
PAYLOAD = {
    "name": "payload",
    "family": "bytes",
    "media_type": "application/json",
    "asset_uri": "file:///records/rec/record.json",
    "served_by": "tegel",
    "content_url": "https://data.example/resources/rec/data/content",
}
DESCRIPTOR = {"parts": [FRAMES, STATS, NOTES], "payload": PAYLOAD}


def _client(handler) -> DataClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    return DataClient("https://data.example", token="token-1", client=http)


def test_capabilities_search_and_bytes() -> None:
    metadata = {
        "uuid": "abc",
        "sample_name": "north",
        "kind": "even",
        "recorded": "2026-10-01",
        "plugin_id": "example-v1",
        "source_name": "abc.json",
    }
    hit = hit_from_metadata(
        "https://data.example/resources/abc",
        metadata,
        PROFILE,
        fallback_id="abc",
    )
    crate = build_search_crate([hit], PROFILE, "https://data.example")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token-1"
        path = request.url.path
        if path == "/capabilities":
            return httpx.Response(
                200,
                json={
                    "spec_version": SPEC_VERSION,
                    "server_version": "0.1.0",
                    "plugins": [],
                    "search_document": "ro-crate",
                    "auth": "keycloak",
                },
            )
        if path == "/search":
            assert request.url.params["kind"] == "even"
            return httpx.Response(
                200,
                content=json.dumps(crate).encode(),
                headers={"content-type": "application/ld+json"},
            )
        if path == "/resources":
            return httpx.Response(
                200,
                json={
                    "resources": [{"id": "abc", "uri": "https://data.example/resources/abc"}],
                    "count": 1,
                },
            )
        if path == "/resources/abc":
            return httpx.Response(
                200,
                json={
                    "id": "abc",
                    "uri": "https://data.example/resources/abc",
                    "metadata": metadata,
                },
            )
        if path == "/resources/abc/metadata":
            return httpx.Response(200, json=metadata)
        if path == "/resources/abc/data":
            payload = {
                **PAYLOAD,
                "content_url": "https://data.example/resources/abc/data/content",
            }
            return httpx.Response(200, json={"parts": [], "payload": payload})
        if path == "/resources/abc/data/content":
            return httpx.Response(200, content=b"payload")
        return httpx.Response(404, json={"detail": "Resource not found"})

    with _client(handler) as client:
        caps = client.capabilities()
        assert caps.auth == "keycloak"
        found = client.search({"kind": "even"})
        assert found.count == 1
        assert found.hits[0].name == "north"
        page = client.browse()
        assert page.count == 1
        resource = client.get("abc")
        assert resource.metadata["kind"] == "even"
        assert client.metadata("abc")["plugin_id"] == "example-v1"
        assert client.data("abc").parts == []
        assert client.read_bytes("abc") == b"payload"


def _data_handler(seen: list[httpx.Request], descriptor: dict, content) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/data"):
            return httpx.Response(200, json=descriptor)
        return content(request)

    return handler


def test_data_lists_parts_without_downloading() -> None:
    seen: list[httpx.Request] = []

    def content(request: httpx.Request) -> httpx.Response:
        raise AssertionError("content was downloaded")

    with _client(_data_handler(seen, DESCRIPTOR, content)) as client:
        descriptor = client.data("rec")
        assert [part.name for part in descriptor.parts] == ["frames", "stats", "notes"]
        assert client.parts("rec")[0].content_url == FRAMES["content_url"]
        assert descriptor.payload is not None
    assert all(request.url.path == "/resources/rec/data" for request in seen)


def test_read_array_sends_slice_and_format() -> None:
    seen: list[httpx.Request] = []

    def content(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00\x01\x02\x03")

    with _client(_data_handler(seen, DESCRIPTOR, content)) as client:
        found = client.read_array("rec", "frames", slice="0:1")
        with pytest.raises(DataClientError, match="not array"):
            client.read_array("rec", "stats")
    assert found.data == b"\x00\x01\x02\x03"
    assert (found.shape, found.data_type) == ([4, 3], "<i4")
    request = seen[1]
    assert str(request.url).startswith(FRAMES["content_url"])
    assert request.url.params["slice"] == "0:1"
    assert request.url.params["format"] == "application/octet-stream"
    assert request.headers["authorization"] == "Bearer token-1"


def test_read_table_columns_and_partition() -> None:
    seen: list[httpx.Request] = []

    def content(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x\n1\n")

    with _client(_data_handler(seen, DESCRIPTOR, content)) as client:
        assert client.read_table("rec", "stats", columns=["x"]) == b"x\n1\n"
        client.read_table("rec", "stats", partition=0)
    whole, partition = seen[1], seen[3]
    assert whole.url.path == "/api/v1/table/full/rec/stats"
    assert whole.url.params.get_list("column") == ["x"]
    assert whole.url.params["format"] == "text/csv"
    assert partition.url.path == "/api/v1/table/partition/rec/stats"
    assert partition.url.params["partition"] == "0"


def test_read_bytes_range_and_payload() -> None:
    seen: list[httpx.Request] = []

    def content(request: httpx.Request) -> httpx.Response:
        return httpx.Response(206, content=b"ab")

    with _client(_data_handler(seen, DESCRIPTOR, content)) as client:
        assert client.read_bytes("rec", "notes", start=2, end=3) == b"ab"
        client.read_bytes("rec", "notes", end=3)
        client.read_bytes("rec", start=5)
    assert seen[1].url.path == "/resources/rec/data/notes/content"
    assert seen[1].headers["range"] == "bytes=2-3"
    assert seen[3].headers["range"] == "bytes=-3"
    assert seen[5].url.path == "/resources/rec/data/content"
    assert seen[5].headers["range"] == "bytes=5-"


def test_client_does_not_leak_token_to_link_origin() -> None:
    seen: list[httpx.Request] = []
    link = {
        **FRAMES,
        "served_by": "link",
        "asset_uri": "https://beamline.example/api/v1/metadata/scan/42/frames",
        "remote": "link",
        "upstream": "https://beamline.example",
        "remote_path": "scan/42/frames",
        "content_url": "https://beamline.example/api/v1/array/full/scan/42/frames",
    }

    def content(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00" * 4)

    handler = _data_handler(seen, {"parts": [link]}, content)
    http = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"Authorization": "Bearer default"}
    )
    with DataClient("https://data.example", token="token-1", client=http) as client:
        client.read_array("rec", "frames")
    assert seen[0].headers["authorization"] == "Bearer token-1"
    assert seen[1].url.host == "beamline.example"
    assert "authorization" not in seen[1].headers

    class Apikey(httpx.Auth):
        def auth_flow(self, request):
            request.headers["Authorization"] = "Apikey beamline"
            yield request

    seen.clear()
    with DataClient(
        "https://data.example",
        token="token-1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        upstream_auth={"https://beamline.example": Apikey()},
    ) as client:
        client.read_array("rec", "frames")
    assert seen[1].headers["authorization"] == "Apikey beamline"


def test_json_search_document_and_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search" and "nope" in request.url.params:
            return httpx.Response(400, json={"detail": "Unknown search field: nope"})
        if request.url.path == "/search":
            body = SearchResult(
                document="listing",
                count=0,
                hits=[],
            ).model_dump()
            return httpx.Response(200, json=body)
        if request.url.path == "/capabilities":
            return httpx.Response(
                200,
                json={
                    "spec_version": "9.0.0",
                    "server_version": "9.0.0",
                    "search_document": "listing",
                    "auth": "keycloak",
                },
            )
        return httpx.Response(401, json={"detail": "Authentication required"})

    with _client(handler) as client:
        found = client.search({})
        assert found.document == "listing"
        assert found.count == 0
        with pytest.raises(DataClientError, match="Unknown search field") as raised:
            client.search({"nope": "x"})
        assert raised.value.status_code == 400
        with pytest.raises(DataClientError, match="incompatible"):
            client.capabilities()


def test_client_credentials_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            body = request.read().decode()
            assert "client_id=etl" in body
            return httpx.Response(200, json={"access_token": "minted"})
        assert request.headers["authorization"] == "Bearer minted"
        return httpx.Response(
            200,
            json={
                "spec_version": SPEC_VERSION,
                "server_version": "0.1.0",
                "search_document": "ro-crate",
                "auth": "keycloak",
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport, base_url="https://data.example")
    with DataClient(
        "https://data.example",
        token_url="https://data.example/token",
        client_id="etl",
        client_secret="secret",
        client=http,
    ) as client:
        assert client.capabilities().server_version == "0.1.0"
