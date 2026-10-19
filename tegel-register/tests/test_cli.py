"""CLI exit codes and the plan/register commands."""
from __future__ import annotations

import json

import numpy as np

from tegel.register.cli import main


def test_proxy_credentials_command_writes_mode_0600(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("TILED_UPSTREAM_API_KEY", raising=False)
    path = tmp_path / "creds.json"
    secret = "upstreamkey"
    code = main(
        [
            "proxy",
            "credentials",
            "--base",
            "http://127.0.0.1:9/api/v1/",
            "--file",
            str(path),
            "--api-key",
            secret,
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert secret not in captured.out
    assert secret not in captured.err
    assert (path.stat().st_mode & 0o777) == 0o600
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == {"http://127.0.0.1:9/api/v1": {"api_key": secret}}
    assert main(["proxy", "credentials", "--base", "http://127.0.0.1:9/api/v1", "--file", str(path)]) == 5


def test_usage_and_binders():
    assert main([]) == 2
    assert main(["plan"]) == 2
    code = main(["binders"])
    assert code == 0


def test_site_check(tmp_path, capsys):
    missing = main(["site", "check", str(tmp_path / "nope.yaml")])
    assert missing == 5
    real = tmp_path / "real"
    incoming = real / "incoming"
    incoming.mkdir(parents=True)
    srvview = tmp_path / "srvview"
    srvview.symlink_to(real, target_is_directory=True)
    site = tmp_path / "site.yaml"
    site.write_text(
        f"version: 1\nreadable:\n  - local: {real}\n    server: {srvview}\n"
        f"landing:\n  local: {incoming}\n  layout: '{{protocol}}/{{sha12}}/{{name}}'\n",
        encoding="utf-8",
    )
    assert main(["site", "check", str(site)]) == 0
    assert "ok sources=0" in capsys.readouterr().out
    absent = tmp_path / "absent.yaml"
    absent.write_text(
        f"version: 1\nreadable:\n  - local: {tmp_path / 'missing'}\n    server: {srvview}\n",
        encoding="utf-8",
    )
    assert main(["site", "check", str(absent)]) == 5
    assert "not a directory" in capsys.readouterr().err
    no_landing = tmp_path / "no-landing.yaml"
    no_landing.write_text(
        f"version: 1\nreadable:\n  - local: {real}\n    server: {srvview}\n"
        f"landing:\n  local: {real / 'incoming-missing'}\n"
        "  layout: '{protocol}/{sha12}/{name}'\n",
        encoding="utf-8",
    )
    assert main(["site", "check", str(no_landing)]) == 5
    assert "landing.local" in capsys.readouterr().err
    no_server = tmp_path / "no-server.yaml"
    no_server.write_text(
        f"version: 1\nreadable:\n  - local: {real}\n    server: {tmp_path / 'missing-srv'}\n",
        encoding="utf-8",
    )
    assert main(["site", "check", str(no_server)]) == 5
    assert "readable[0].server" in capsys.readouterr().err


def test_plan_and_register(tiled_site, tmp_path, capsys, monkeypatch):
    client, _site, real, srvview, server = tiled_site
    array = real / "sample.npy"
    np.save(array, np.arange(4, dtype=np.float32))
    site = tmp_path / "site.yaml"
    site.write_text(
        "\n".join(
            [
                "version: 1",
                "target:",
                f"  base_uri: {str(client.context.api_uri).rstrip('/')}",
                "  secret_id: target",
                "readable:",
                f"  - local: {real}",
                f"    server: {srvview}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TILED_API_KEY", server.api_key)
    args = ["--site", str(site), "--into", str(client.context.api_uri).rstrip("/")]
    assert main(["plan", array.as_uri(), *args]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["origin"]["mode"] == "reference"
    assert main(["register", array.as_uri(), *args]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["created"] is True
    assert created["mode"] == "reference"
    assert np.array_equal(np.asarray(client["sample"].read()), np.arange(4, dtype=np.float32))


def test_infeasible_mode_is_bind_error(tiled_site, tmp_path, monkeypatch):
    client, _site, real, srvview, server = tiled_site
    array = real / "sample.npy"
    np.save(array, np.arange(2, dtype=np.float32))
    site = tmp_path / "site.yaml"
    site.write_text(
        "\n".join(
            [
                "version: 1",
                "target:",
                f"  base_uri: {str(client.context.api_uri).rstrip('/')}",
                "readable:",
                f"  - local: {real}",
                f"    server: {srvview}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TILED_API_KEY", server.api_key)
    code = main(
        [
            "plan",
            array.as_uri(),
            "--site",
            str(site),
            "--into",
            str(client.context.api_uri).rstrip("/"),
            "--mode",
            "existing",
        ]
    )
    assert code == 3
