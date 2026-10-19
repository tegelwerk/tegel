"""PROXY and EXISTING binds. The upstream server is a subprocess (shared auth settings)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from collections import ChainMap

import pytest

pytest.importorskip("tiled.server")

import numpy as np

from tegel.register import register
from tegel.register.context import BindContext
from tegel.register.errors import SecretLookupError
from tegel.register.site import Site
from tegel.register.tiled_server.credentials import ENV, api_key_for
from tegel.register.tiled_server.proxy import (
    PROXY_ARRAY_MIMETYPE,
    PROXY_TABLE_MIMETYPE,
    RemoteArrayAdapter,
    RemoteTableAdapter,
    clear_client_cache,
)

_UPSTREAM = textwrap.dedent(
    """
    import numpy as np
    import pandas as pd
    from tiled.client import from_uri
    from tiled.server import SimpleTiledServer

    server = SimpleTiledServer(api_key="upstreamtestkey")
    client = from_uri(server.uri)
    client.write_array(np.arange(48, dtype=np.float32).reshape(6, 8), key="img")
    client.write_table(
        pd.DataFrame({"a": range(10), "b": range(10, 20), "c": range(20, 30)}),
        key="tab",
    )
    print(server.uri, flush=True)
    raise SystemExit(server._server.thread.join())
    """
)


@pytest.fixture(scope="module")
def upstream_tiled():
    proc = subprocess.Popen(
        [sys.executable, "-c", _UPSTREAM],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    if not line:
        err = proc.stderr.read() if proc.stderr is not None else ""
        proc.kill()
        pytest.fail(f"upstream tiled did not start\n{err}")
    try:
        yield line
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture
def proxy_target(tiled_site, tmp_path, upstream_tiled):
    from urllib.parse import parse_qs, urlparse

    client, _site, _real, _srvview, server = tiled_site
    parsed = urlparse(upstream_tiled)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    api_key = parse_qs(parsed.query)["api_key"][0]
    creds = tmp_path / "proxy-creds.json"
    creds.write_text(json.dumps({base: {"api_key": api_key}}), encoding="utf-8")
    os.chmod(creds, 0o600)
    os.environ[ENV] = str(creds)
    clear_client_cache()
    server.catalog.context.adapters_by_mimetype = ChainMap(
        {
            PROXY_ARRAY_MIMETYPE: RemoteArrayAdapter,
            PROXY_TABLE_MIMETYPE: RemoteTableAdapter,
        },
        server.catalog.context.adapters_by_mimetype,
    )
    site = Site.from_mapping(
        {
            "version": 1,
            "sources": {
                "lab.tiled": {
                    "protocol": "tiled",
                    "base_uri": base,
                    "proxy": True,
                    "secret_id": "upstream",
                }
            },
        }
    )

    class Secrets:
        def get_secret(self, secret_id: str):
            return {"api_key": api_key}

    ctx = BindContext(site=site, secrets=Secrets())
    yield client, ctx, base, api_key
    clear_client_cache()


def test_keyless_client_reads_proxied_array(proxy_target, tmp_path, monkeypatch, capsys):
    from tiled.client import from_uri

    from tegel.register.cli import main

    client, ctx, base, api_key = proxy_target
    creds = tmp_path / "server-creds.json"
    code = main(
        ["proxy", "credentials", "--base", base, "--file", str(creds), "--api-key", api_key]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert api_key not in captured.out
    assert api_key not in captured.err
    monkeypatch.setenv(ENV, str(creds))
    clear_client_cache()
    register("lab.tiled://img", client, key="img-keyless", context=ctx)
    target_key = client.context.api_key
    assert target_key != api_key
    reader = from_uri(str(client.uri), api_key=target_key)
    assert reader.context.api_key == target_key
    assert api_key not in str(reader.uri)
    assert api_key not in (reader.context.api_key or "")
    node = reader["img-keyless"]
    metadata = json.dumps(node.metadata, default=str)
    assert api_key not in metadata
    expected = np.arange(48, dtype=np.float32).reshape(6, 8)
    assert np.array_equal(np.asarray(node.read()), expected)


def test_proxy_array_full_slice_and_block(proxy_target):
    client, ctx, _base, _key = proxy_target
    binding = register("lab.tiled://img", client, context=ctx)
    assert binding.origin.mode.value == "proxy"
    expected = np.arange(48, dtype=np.float32).reshape(6, 8)
    assert np.array_equal(np.asarray(binding.node.read()), expected)
    assert np.array_equal(np.asarray(binding.node[2:4, 1:5]), expected[2:4, 1:5])
    block = np.asarray(binding.node.read_block((0, 0)))
    assert block.shape[0] > 0
    assert np.array_equal(block, expected[tuple(slice(0, size) for size in block.shape)])


def test_proxy_table(proxy_target):
    client, ctx, _base, _key = proxy_target
    binding = register("lab.tiled://tab", client, context=ctx)
    table = binding.node.read()
    assert list(table["a"]) == list(range(10))
    subset = binding.node.read(columns=["b"])
    assert "a" not in getattr(subset, "columns", [])
    part = binding.node.read_partition(0)
    assert len(part) == 10 or getattr(part, "num_rows", 10) == 10


def test_upstream_structure_drift_fails_the_read(proxy_target, upstream_tiled):
    from tiled.client import from_uri

    client, ctx, _base, _key = proxy_target
    binding = register("lab.tiled://img", client, key="img-proxy", context=ctx)
    upstream = from_uri(upstream_tiled)
    upstream["img"].delete(external_only=False)
    upstream.write_array(np.zeros((2, 2), dtype=np.float32), key="img")
    with pytest.raises(Exception) as raised:
        binding.node.read()
    assert "upstreamtestkey" not in str(raised.value)


def test_proxy_credentials_file_mode_and_missing_entry(tmp_path, monkeypatch):
    secret = "SENTINEL-DO-NOT-LEAK-proxy"
    loose = tmp_path / "loose.json"
    loose.write_text(json.dumps({"http://upstream": {"api_key": secret}}), encoding="utf-8")
    os.chmod(loose, 0o644)
    monkeypatch.setenv(ENV, str(loose))
    with pytest.raises(SecretLookupError) as raised:
        api_key_for("http://upstream")
    assert secret not in str(raised.value)
    tight = tmp_path / "tight.json"
    tight.write_text(json.dumps({"http://other": {"api_key": secret}}), encoding="utf-8")
    os.chmod(tight, 0o600)
    monkeypatch.setenv(ENV, str(tight))
    assert api_key_for("http://missing") is None


def test_wrong_proxy_key_is_not_in_the_error(proxy_target, tmp_path, monkeypatch):
    client, ctx, base, _api_key = proxy_target
    bad = "SENTINEL-DO-NOT-LEAK-wrong"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({base: {"api_key": bad}}), encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setenv(ENV, str(path))
    clear_client_cache()
    binding = register("lab.tiled://img", client, key="img-bad", context=ctx)
    with pytest.raises(Exception) as raised:
        binding.node.read()
    assert bad not in str(raised.value)
    assert bad not in repr(raised.value)


def test_existing_returns_the_node(tiled_site):
    client, _site, real, _srvview, server = tiled_site
    array = np.arange(4, dtype=np.float32)
    client.write_array(array, key="already")
    before = sorted(client)
    base = str(client.context.api_uri).rstrip("/")
    merged = Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(real), "server": "/srv"}],
            "sources": {
                "lab.tiled": {
                    "protocol": "tiled",
                    "base_uri": base,
                    "proxy": False,
                    "secret_id": "target",
                }
            },
        }
    )

    class Secrets:
        def get_secret(self, secret_id: str):
            return {"api_key": server.api_key}

    ctx = BindContext(site=merged, secrets=Secrets())
    binding = register("lab.tiled://already", client, context=ctx)
    assert binding.created is False
    assert binding.path == "/already"
    assert sorted(client) == before
    assert np.array_equal(np.asarray(binding.node.read()), array)


def test_tiled_server_imports_do_not_pull_binders():
    code = textwrap.dedent(
        """
        import sys
        import tegel.register.tiled_server.proxy
        forbidden = ["tegel.binders", "tegel.register.site", "globus_sdk"]
        loaded = [name for name in forbidden if name in sys.modules or any(mod.startswith(name + ".") for mod in sys.modules)]
        if loaded:
            raise SystemExit("loaded " + ",".join(loaded))
        """
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
