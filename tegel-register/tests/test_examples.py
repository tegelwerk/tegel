"""The shipped examples run. Live ones skip unless their env var is set."""
from __future__ import annotations

import io
import os
import runpy
import subprocess
import sys
import textwrap
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _run(script: str, env: dict[str, str]) -> str:
    merged = os.environ.copy()
    for key in (
        "TEGEL_REGISTER_SITE",
        "TEGEL_REGISTER_FILE_URI",
        "TEGEL_REGISTER_INTO",
        "TEGEL_REGISTER_TILED_URI",
        "TEGEL_REGISTER_GLOBUS_LIVE",
        "TEGEL_REGISTER_ZENODO",
        "TEGEL_REGISTER_GLOBUS_CLIENT_ID",
        "TILED_UPSTREAM_API_KEY",
    ):
        merged.pop(key, None)
    merged.update(env)
    old = os.environ.copy()
    old_argv = sys.argv
    os.environ.clear()
    os.environ.update(merged)
    sys.argv = [script]
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            try:
                runpy.run_path(str(EXAMPLES / script), run_name="__main__")
            except SystemExit as exc:
                if exc.code not in (0, None):
                    raise
    finally:
        os.environ.clear()
        os.environ.update(old)
        sys.argv = old_argv
    return buffer.getvalue()


def test_live_examples_skip_without_env():
    assert "skip:" in _run("acquire_globus.py", {})
    assert "skip:" in _run("acquire_zenodo.py", {})
    assert "skip:" in _run("globus/login.py", {})
    assert "skip:" in _run("proxy.py", {})


def test_reference_example_reads_the_file_bytes():
    payload = np.arange(4, dtype=np.float32).tobytes().hex()
    output = _run("reference.py", {})
    assert payload in output
    assert "/sample" in output


def test_proxy_example_registers(tiled_site, tmp_path, monkeypatch):
    from collections import ChainMap

    from tegel.register.tiled_server.credentials import ENV
    from tegel.register.tiled_server.proxy import PROXY_ARRAY_MIMETYPE, RemoteArrayAdapter, clear_client_cache

    client, _site, real, srvview, server = tiled_site
    base, api_key, proc = _upstream()
    try:
        creds = tmp_path / "creds.json"
        creds.write_text('{"%s": {"api_key": "%s"}}\n' % (base, api_key), encoding="utf-8")
        os.chmod(creds, 0o600)
        monkeypatch.setenv(ENV, str(creds))
        clear_client_cache()
        server.catalog.context.adapters_by_mimetype = ChainMap(
            {PROXY_ARRAY_MIMETYPE: RemoteArrayAdapter},
            server.catalog.context.adapters_by_mimetype,
        )
        site = tmp_path / "site.yaml"
        site.write_text(
            "\n".join(
                [
                    "version: 1",
                    "readable:",
                    f"  - local: {real}",
                    f"    server: {srvview}",
                    "sources:",
                    "  lab.tiled:",
                    "    protocol: tiled",
                    f"    base_uri: {base}",
                    "    proxy: true",
                    "    secret_id: upstream",
                ]
            ),
            encoding="utf-8",
        )
        output = _run(
            "proxy.py",
            {
                "TEGEL_REGISTER_SITE": str(site),
                "TEGEL_REGISTER_TILED_URI": "lab.tiled://img",
                "TEGEL_REGISTER_INTO": server.uri,
                "TILED_UPSTREAM_API_KEY": api_key,
                ENV: str(creds),
            },
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
    assert "/img" in output
    assert "img" in list(client)


def test_example_site_and_server_config_load():
    from tegel.register.cli import main
    from tegel.register.site import Site

    site = Site.load(str(EXAMPLES / "site.example.yaml"))
    assert site.sources["lab.globus"].params["secret_id"] == "globus-example"
    assert site.readable[0].local.name == "real"
    assert site.readable[0].server.name == "srvview"
    assert Path(str(site.readable[0].server)).is_symlink()
    assert main(["site", "check", str(EXAMPLES / "site.example.yaml")]) == 0
    server = yaml.safe_load((EXAMPLES / "tiled-server.example.yml").read_text(encoding="utf-8"))
    assert server["trees"][0]["args"]["readable_storage"] == ["local/srvview"]
    adapters = server["trees"][0]["args"]["adapters_by_mimetype"]
    assert "tegel.register.tiled_server.proxy:RemoteArrayAdapter" in adapters.values()
    assert "tegel.register.tiled_server.proxy:RemoteTableAdapter" in adapters.values()
    key = server["authentication"]["single_user_api_key"]
    assert isinstance(key, str) and key.isalnum() and len(set(key)) == 1
    root = EXAMPLES
    storage = server["trees"][0]["args"]["readable_storage"] + server["trees"][0]["args"]["writable_storage"]
    for item in storage:
        path = Path(item)
        if not path.is_absolute():
            path = Path(os.path.normpath(root / path))
        assert path.is_dir()


def _upstream():
    script = textwrap.dedent(
        """
        import numpy as np
        from tiled.client import from_uri
        from tiled.server import SimpleTiledServer
        server = SimpleTiledServer(api_key="upstreamkey")
        client = from_uri(server.uri)
        client.write_array(np.arange(6, dtype=np.float32).reshape(2, 3), key="img")
        print(server.uri, flush=True)
        raise SystemExit(server._server.thread.join())
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    if not line:
        err = proc.stderr.read() if proc.stderr is not None else ""
        proc.kill()
        raise RuntimeError(err)
    parsed = urlparse(line)
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    api_key = parse_qs(parsed.query)["api_key"][0]
    return base, api_key, proc
