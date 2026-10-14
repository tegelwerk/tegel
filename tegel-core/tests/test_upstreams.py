"""The upstreams file, URL splitting, and upstream credentials."""

from pathlib import Path

import httpx
import pytest

from tegel.core.upstreams import (
    Upstream,
    UpstreamAuth,
    load_upstreams,
    split_remote,
    upstream_timeout,
)

FILE = """
upstreams:
  - origin: https://Beamline.example/
    auth: api_key
    api_key_env: BEAMLINE_KEY
  - origin: http://other.example:8000
    api_prefix: tiled/api/v1/
"""


def test_load_upstreams_reads_secrets_from_env_only(tmp_path: Path) -> None:
    path = tmp_path / "upstreams.yaml"
    path.write_text(FILE, encoding="utf-8")
    found = load_upstreams(path, {"BEAMLINE_KEY": "k"})
    assert sorted(found) == ["http://other.example:8000", "https://beamline.example"]
    assert found["http://other.example:8000"].base == "http://other.example:8000/tiled/api/v1"
    with pytest.raises(ValueError, match="needs BEAMLINE_KEY"):
        load_upstreams(path, {})
    assert load_upstreams(None, {}) == {}
    path.write_text(FILE + "    api_key: inline\n", encoding="utf-8")
    with pytest.raises(ValueError, match="api_key"):
        load_upstreams(path, {"BEAMLINE_KEY": "k"})


def test_upstream_rejects_paths_and_missing_auth_fields() -> None:
    with pytest.raises(ValueError, match="must not carry a path"):
        Upstream(origin="https://beamline.example/api")
    with pytest.raises(ValueError, match="needs api_key_env"):
        Upstream(origin="https://beamline.example", auth="api_key")
    with pytest.raises(ValueError, match="token_url, client_id, client_secret_env"):
        Upstream(origin="https://beamline.example", auth="client_credentials")


def test_split_remote() -> None:
    upstreams = {"https://beamline.example": Upstream(origin="https://beamline.example")}
    upstream, path = split_remote(
        "https://beamline.example/api/v1/metadata/scan/42/frames", upstreams
    )
    assert (upstream.origin, path) == ("https://beamline.example", "scan/42/frames")
    for url, message in (
        ("https://elsewhere.example/api/v1/metadata/x", "not listed"),
        ("https://beamline.example/api/v1/array/full/x", "must start with /api/v1/metadata/"),
        ("https://beamline.example/api/v1/metadata/a/../b", "not normalized"),
        ("https://beamline.example/api/v1/metadata/x?slice=0", "no query"),
    ):
        with pytest.raises(ValueError, match=message):
            split_remote(url, upstreams)


def test_timeout_default_and_override() -> None:
    assert upstream_timeout({}) == 10.0
    assert upstream_timeout({"TEGEL_UPSTREAM_TIMEOUT": "2.5"}) == 2.5


def test_client_credentials_token_is_cached() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "minted", "expires_in": 300})
        return httpx.Response(200, json={})

    upstream = Upstream(
        origin="https://beamline.example",
        auth="client_credentials",
        token_url="https://auth.example/token",
        client_id="tegel",
        client_secret_env="SECRET",
    )
    auth = UpstreamAuth(upstream, {"SECRET": "s"})
    with httpx.Client(transport=httpx.MockTransport(handler), auth=auth) as client:
        client.get("https://beamline.example/api/v1/metadata/x")
        client.get("https://beamline.example/api/v1/metadata/y")
    assert [request.url.path for request in seen].count("/token") == 1
    assert b"client_secret=s" in seen[0].content
    assert seen[1].headers["authorization"] == "Bearer minted"
    assert seen[2].headers["authorization"] == "Bearer minted"
