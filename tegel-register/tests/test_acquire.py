"""ACQUIRE for file, tiled, zenodo, and globus. No live network."""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tegel.register import plan, register
from tegel.register.bind import Mode
from tegel.register.context import BindContext
from tegel.register.errors import (
    AcquireError,
    AcquireTimeoutError,
    BindError,
    InvalidURIError,
    SiteConfigError,
    UnknownSchemeError,
)
from tegel.register.site import Site

SENTINEL = "SENTINEL-DO-NOT-LEAK-"


def _landing_map(real, extra=None):
    incoming = real / "incoming"
    incoming.mkdir(exist_ok=True)
    landing = {
        "local": str(incoming),
        "layout": "{protocol}/{sha12}/{name}",
    }
    if extra:
        landing.update(extra)
    return landing


def _assert_clean(*texts: str) -> None:
    for text in texts:
        assert SENTINEL not in text


class _FakeTransfer:
    def __init__(self, *, status="SUCCEEDED", finish=True, write=None, events=None, rows=None):
        self.status = status
        self.finish = finish
        self.write = write
        self.events = events or []
        self.rows = rows if rows is not None else []
        self.cancelled: list[str] = []
        self.submission = None
        self.waits: list[tuple] = []

    def submit_transfer(self, submission):
        self.submission = submission
        return {"task_id": "task-1"}

    def task_wait(self, task_id, *, timeout, polling_interval):
        self.waits.append((timeout, polling_interval))
        if self.write is not None:
            self.write()
        return self.finish

    def get_task(self, task_id):
        return {"status": self.status}

    def cancel_task(self, task_id):
        self.cancelled.append(task_id)

    def task_event_list(self, task_id):
        return self.events

    def operation_ls(self, collection, **kwargs):
        return self.rows


def _globus_site(real, srvview):
    return Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(real), "server": str(srvview)}],
            "landing": _landing_map(
                real,
                {"globus": {"collection": "dest-collection", "path": "/landing"}},
            ),
            "sources": {
                "lab.globus": {
                    "protocol": "globus",
                    "collection": "src-collection",
                    "secret_id": "one",
                    "secrets": {"/data": "two"},
                }
            },
        }
    )


class _Secrets:
    def get_secret(self, secret_id: str):
        return {
            "client_id": "client",
            "refresh_token": f"{SENTINEL}{secret_id}",
        }


def test_file_auto_outside_readable_acquires(tiled_site, tmp_path):
    import numpy as np

    client, _site, real, srvview, _server = tiled_site
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "sample.npy"
    np.save(source, np.arange(6, dtype=np.float32))
    site = Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(real), "server": str(srvview)}],
            "landing": _landing_map(real),
        }
    )

    class Secrets:
        def get_secret(self, secret_id: str):
            return {"token": f"{SENTINEL}file"}

    ctx = BindContext(site=site, secrets=Secrets())
    uri = source.as_uri()
    planned = plan(uri, client, mode="auto", context=ctx)
    assert planned.origin.mode is Mode.ACQUIRE
    assert planned.moves_bytes is True
    assert planned.size_bytes == source.stat().st_size
    assert not any((real / "incoming").rglob("sample.npy"))
    _assert_clean(planned.to_json(), repr(planned))
    binding = register(uri, client, mode="auto", context=ctx)
    assert binding.origin.mode is Mode.ACQUIRE
    assert binding.origin.acquired_from == uri
    assert binding.created is True
    assert np.array_equal(np.asarray(binding.node.read()), np.arange(6, dtype=np.float32))


def test_tiled_acquire_exports_array_and_table(tiled_site):
    import numpy as np
    import pandas as pd

    client, _site, real, srvview, server = tiled_site
    client.write_array(np.arange(6, dtype=np.float32), key="img")
    client.write_table(pd.DataFrame({"a": [1, 2, 3]}), key="tab")
    base = str(client.context.api_uri).rstrip("/")
    site = Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(real), "server": str(srvview)}],
            "landing": _landing_map(real),
            "sources": {
                "lab.tiled": {
                    "protocol": "tiled",
                    "base_uri": base,
                    "secret_id": "target",
                }
            },
        }
    )

    class Secrets:
        def get_secret(self, secret_id: str):
            return {"api_key": server.api_key, "note": f"{SENTINEL}tiled"}

    ctx = BindContext(site=site, secrets=Secrets())
    planned = plan("lab.tiled://img", client, key="img-copy", mode="acquire", context=ctx)
    assert "array acquire reads the whole array" in planned.notes
    assert planned.moves_bytes is True
    assert planned.size_bytes == 6 * 4  # six float32 values upstream
    _assert_clean(planned.to_json(), repr(planned))
    array = register("lab.tiled://img", client, key="img-copy", mode="acquire", context=ctx)
    assert array.origin.mode is Mode.ACQUIRE
    assert array.origin.acquired_from == "lab.tiled://img"
    assert np.array_equal(np.asarray(array.node.read()), np.arange(6, dtype=np.float32))
    table = register("lab.tiled://tab", client, key="tab-copy", mode="acquire", context=ctx)
    assert list(table.node.read()["a"]) == [1, 2, 3]


def test_zenodo_loopback_lands_and_rejects_bad_checksums(tiled_site, monkeypatch):
    body = b"a,b\n1,2\n"
    digest = hashlib.md5(body).hexdigest()
    client, _site, real, srvview, _server = tiled_site
    site = Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(real), "server": str(srvview)}],
            "landing": _landing_map(real),
            "sources": {"https": {"protocol": "zenodo", "host": "zenodo.org"}},
        }
    )
    ctx = BindContext(site=site)
    base = _serve(body, size=len(body), md5=digest)
    monkeypatch.setattr("tegel.register.binders.zenodo._API", base)
    binding = register("zenodo:/records/42/sample.csv", client, context=ctx)
    assert binding.origin.mode is Mode.ACQUIRE
    assert list(binding.node.read()["a"]) == [1]

    bad = site.landing_path("zenodo", "zenodo:/records/42/bad.csv", "bad.csv")
    mismatch = _serve(body, size=len(body), md5="0" * 32, filename="bad.csv", record="9")
    monkeypatch.setattr("tegel.register.binders.zenodo._API", mismatch)
    with pytest.raises(AcquireError) as raised:
        register("zenodo:/records/9/bad.csv", client, context=ctx)
    _assert_clean(str(raised.value), repr(raised.value))
    assert not bad.exists()
    assert not bad.with_name(bad.name + ".partial").exists()

    oversized = _serve(body, size=2, md5=digest, filename="big.csv", record="8")
    monkeypatch.setattr("tegel.register.binders.zenodo._API", oversized)
    with pytest.raises(AcquireError):
        register("zenodo:/records/8/big.csv", client, context=ctx)
    landed = site.landing_path("zenodo", "zenodo:/records/8/big.csv", "big.csv")
    assert not landed.exists()


def test_https_zenodo_is_opt_in(tmp_path):
    root = tmp_path / "real"
    root.mkdir()
    site = Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(root), "server": "/data"}],
            "landing": _landing_map(root),
        }
    )
    ctx = BindContext(site=site)
    with pytest.raises(UnknownSchemeError):
        plan("https://zenodo.org/records/1/a.csv", object(), context=ctx)
    mapped = Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(root), "server": "/data"}],
            "landing": _landing_map(root),
            "sources": {"https": {"protocol": "zenodo", "host": "example.com"}},
        }
    )
    with pytest.raises(SiteConfigError):
        plan("https://example.com/records/1/a.csv", object(), context=BindContext(site=mapped))
    ok = Site.from_mapping(
        {
            "version": 1,
            "readable": [{"local": str(root), "server": "/data"}],
            "landing": _landing_map(root),
            "sources": {"https": {"protocol": "zenodo", "host": "zenodo.org"}},
        }
    )
    with pytest.raises(InvalidURIError):
        plan(
            "https://example.com/records/1/a.csv",
            object(),
            context=BindContext(site=ok),
        )


def test_globus_fake_success_failure_timeout_and_missing(tiled_site, monkeypatch):
    client, _site, real, srvview, _server = tiled_site
    site = _globus_site(real, srvview)
    ctx = BindContext(site=site, secrets=_Secrets())
    uri = "lab.globus:/data/sample.csv"
    landing = site.landing_path("globus", uri, "sample.csv")
    tmp = landing.with_name(landing.name + ".partial")
    fake = _FakeTransfer(write=lambda: tmp.write_text("a,b\n1,2\n", encoding="utf-8"))
    monkeypatch.setattr("tegel.register.binders.globus._transfer_client", lambda secret: fake)
    planned = plan(uri, client, mode="acquire", context=ctx)
    _assert_clean(planned.to_json(), repr(planned))
    binding = register(uri, client, mode="acquire", context=ctx)
    assert binding.origin.mode is Mode.ACQUIRE
    assert fake.submission.verify_checksum is True
    assert fake.submission.items[0].destination_path.endswith(".partial")
    assert list(binding.node.read()["a"]) == [1]
    for timeout, poll in fake.waits:
        assert isinstance(timeout, int) and timeout >= 1
        assert isinstance(poll, int) and poll >= 1

    failed = _FakeTransfer(
        status="FAILED",
        events=[{"is_fatal": True, "description": f"nope {SENTINEL}one and {SENTINEL}two"}],
    )
    failed_ctx = BindContext(site=site, secrets=_Secrets())
    monkeypatch.setattr("tegel.register.binders.globus._transfer_client", lambda secret: failed)
    with pytest.raises(AcquireError) as raised:
        register("lab.globus:/data/other.csv", client, mode="acquire", context=failed_ctx)
    _assert_clean(str(raised.value), repr(raised.value))

    timed = _FakeTransfer(finish=False)
    fresh = BindContext(site=site, secrets=_Secrets())
    monkeypatch.setattr("tegel.register.binders.globus._transfer_client", lambda secret: timed)
    with pytest.raises(AcquireTimeoutError):
        register("lab.globus:/data/slow.csv", client, mode="acquire", context=fresh, acquire_timeout=0.01)
    assert timed.cancelled == ["task-1"]
    assert timed.waits[0][0] >= 1 and isinstance(timed.waits[0][0], int)

    missing = _FakeTransfer()
    again = BindContext(site=site, secrets=_Secrets())
    monkeypatch.setattr("tegel.register.binders.globus._transfer_client", lambda secret: missing)
    with pytest.raises(SiteConfigError) as missing_error:
        register("lab.globus:/data/gone.csv", client, mode="acquire", context=again)
    _assert_clean(str(missing_error.value), repr(missing_error.value))


def test_globus_directory_needs_recursive(tmp_path, monkeypatch):
    root = tmp_path / "real"
    root.mkdir()
    site = _globus_site(root, root)
    ctx = BindContext(site=site, secrets=_Secrets())
    fake = _FakeTransfer(rows=[{"name": "folder", "type": "dir"}])
    monkeypatch.setattr("tegel.register.binders.globus._transfer_client", lambda secret: fake)
    with pytest.raises(BindError, match="directory needs \\?recursive"):
        plan("lab.globus:/folder", object(), context=ctx)


def test_task_wait_passes_integers(monkeypatch):
    globus_sdk = pytest.importorskip("globus_sdk")
    recorded: list[tuple] = []

    def task_wait(self, task_id, *, timeout=10, polling_interval=10):
        recorded.append((timeout, polling_interval))
        return True

    monkeypatch.setattr(globus_sdk.TransferClient, "task_wait", task_wait)
    client = globus_sdk.TransferClient(authorizer=globus_sdk.AccessTokenAuthorizer("x"))
    client.get_task = lambda task_id: {"status": "SUCCEEDED"}
    from tegel.register.binders.globus import _wait

    for timeout in (None, 0.01, 5.5, 3600):
        _wait(client, "task-1", timeout, values=set(), uri="lab.globus:/data/a")
    assert len(recorded) == 4
    for timeout, poll in recorded:
        assert isinstance(timeout, int) and timeout >= 1
        assert isinstance(poll, int) and poll >= 1
    assert recorded[0][0] == 3600
    assert recorded[1][0] == 1
    assert recorded[2][0] == 5
    assert recorded[3][0] == 3600


def _serve(body: bytes, *, size: int, md5: str, filename: str = "sample.csv", record: str = "42") -> str:
    payload = json.dumps(
        {
            "files": [
                {
                    "key": filename,
                    "size": size,
                    "checksum": f"md5:{md5}",
                    "links": {"self": "PLACEHOLDER"},
                }
            ]
        }
    ).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            host, port = self.server.server_address[:2]
            if self.path == f"/api/records/{record}":
                text = payload.replace(
                    b"PLACEHOLDER",
                    f"http://{host}:{port}/files/{filename}".encode("ascii"),
                )
                self._send(text, "application/json")
                return
            if self.path == f"/files/{filename}":
                self._send(body, "text/csv")
                return
            self.send_response(404)
            self.end_headers()

        def _send(self, data: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    return f"http://{host}:{port}"
