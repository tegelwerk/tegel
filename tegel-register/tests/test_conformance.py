"""Conformance suite against the four built-in binders."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import numpy as np

from tegel.register.bind import Mode
from tegel.register.binders.file import FileBinder
from tegel.register.binders.globus import GlobusBinder
from tegel.register.binders.tiled import TiledBinder
from tegel.register.binders.zenodo import ZenodoBinder
from tegel.register.context import BindContext
from tegel.register.site import Site
from tegel.register.testing import run_conformance
from tegel.register.testing.conformance import ConformanceCase

_spec = importlib.util.spec_from_file_location(
    "acquire_helpers",
    Path(__file__).with_name("test_acquire.py"),
)
_helpers = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_helpers)
_FakeTransfer = _helpers._FakeTransfer
_landing_map = _helpers._landing_map
_serve = _helpers._serve

SENTINEL = "SENTINEL-DO-NOT-LEAK-"


class _Secrets:
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def get_secret(self, secret_id: str):
        secret = {"token": f"{SENTINEL}{secret_id}", "client_id": "client", "refresh_token": f"{SENTINEL}{secret_id}"}
        if self._api_key:
            secret["api_key"] = self._api_key
        return secret


def _make_site(real, srvview, *, sources=None, globus=False):
    extra = {"globus": {"collection": "dest-collection", "path": "/landing"}} if globus else None
    data = {
        "version": 1,
        "readable": [{"local": str(real), "server": str(srvview)}],
        "landing": _landing_map(real, extra),
    }
    if sources:
        data["sources"] = sources
    return Site.from_mapping(data)


def test_file_conformance(tiled_site, tmp_path):
    client, _site, real, srvview, _server = tiled_site
    inside = real / "inside.npy"
    np.save(inside, np.arange(3, dtype=np.float32))
    outside = tmp_path / "outside.npy"
    np.save(outside, np.arange(3, dtype=np.float32))
    site = _make_site(real, srvview)
    case = ConformanceCase(
        binder=FileBinder(),
        uri=inside.as_uri(),
        into=client,
        context=BindContext(site=site, secrets=_Secrets()),
        infeasible=Mode.EXISTING,
        sentinel=SENTINEL,
        acquire_uri=outside.as_uri(),
        landing=real / "incoming",
    )
    result = run_conformance(case)
    assert result.failed == []
    assert "acquire_atomic" in result.passed


def test_tiled_conformance(tiled_site):
    client, _site, real, srvview, server = tiled_site
    client.write_array(np.arange(4, dtype=np.float32), key="src")
    base = str(client.context.api_uri).rstrip("/")
    site = _make_site(
        real,
        srvview,
        sources={"lab.tiled": {"protocol": "tiled", "base_uri": base, "secret_id": "target"}},
    )
    case = ConformanceCase(
        binder=TiledBinder(),
        uri="lab.tiled://src",
        into=client,
        context=BindContext(site=site, secrets=_Secrets(server.api_key)),
        infeasible=Mode.REFERENCE,
        sentinel=SENTINEL,
        acquire_uri="lab.tiled://src",
        acquire_key="src-landed",
        landing=real / "incoming",
    )
    result = run_conformance(case)
    assert result.failed == []


def test_globus_conformance(tiled_site, monkeypatch):
    client, _site, real, srvview, _server = tiled_site
    site = _make_site(
        real,
        srvview,
        globus=True,
        sources={
            "lab.globus": {
                "protocol": "globus",
                "collection": "src-collection",
                "secret_id": "one",
            }
        },
    )
    uri = "lab.globus:/data/sample.csv"
    dest = site.landing_path("globus", uri, "sample.csv")
    tmp = dest.with_name(dest.name + ".partial")
    fake = _FakeTransfer(write=lambda: tmp.write_text("a,b\n1,2\n", encoding="utf-8"))
    monkeypatch.setattr("tegel.register.binders.globus._transfer_client", lambda secret: fake)
    case = ConformanceCase(
        binder=GlobusBinder(),
        uri=uri,
        into=client,
        context=BindContext(site=site, secrets=_Secrets()),
        infeasible=Mode.EXISTING,
        sentinel=SENTINEL,
        acquire_uri=uri,
        landing=real / "incoming",
    )
    result = run_conformance(case)
    assert result.failed == []


def test_zenodo_conformance(tiled_site, monkeypatch):
    client, _site, real, srvview, _server = tiled_site
    body = b"a,b\n1,2\n"
    base = _serve(body, size=len(body), md5=hashlib.md5(body).hexdigest())
    monkeypatch.setattr("tegel.register.binders.zenodo._API", base)
    site = _make_site(real, srvview)
    uri = "zenodo:/records/42/sample.csv"
    case = ConformanceCase(
        binder=ZenodoBinder(),
        uri=uri,
        into=client,
        context=BindContext(site=site, secrets=_Secrets()),
        infeasible=Mode.PROXY,
        sentinel=SENTINEL,
        acquire_uri=uri,
        landing=real / "incoming",
    )
    result = run_conformance(case)
    assert result.failed == []
