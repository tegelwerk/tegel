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
            return httpx.Response(
                200,
                json={
                    "family": "bytes",
                    "media_type": "application/octet-stream",
                    "content_url": "https://data.example/resources/abc/data/content",
                },
            )
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
        descriptor = client.open_data("abc")
        assert descriptor.family.value == "bytes"
        assert client.read_bytes("abc") == b"payload"


def test_array_descriptor_is_not_downloaded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/data/content"):
            raise AssertionError("array content was downloaded")
        return httpx.Response(
            200,
            json={
                "family": "array",
                "media_type": "application/octet-stream",
                "shape": [4, 8, 1024],
                "chunks": [[1, 8, 128]],
                "data_type": "float32",
            },
        )

    with _client(handler) as client:
        descriptor = client.open_data("cube")
        assert descriptor.chunks == [[1, 8, 128]]
        with pytest.raises(DataClientError, match="bytes"):
            client.read_bytes("cube")


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
