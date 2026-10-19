#!/usr/bin/env python3
"""Register a file the local server can read, then read those bytes back.

Bytes live in ``examples/local/real``. ``examples/local/srvview`` is a symlink
to that directory and is the readable storage in ``tiled-server.example.yml``.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import yaml
from tiled.client import from_uri
from tiled.server import SimpleTiledServer

from tegel.register import register
from tegel.register.context import BindContext
from tegel.register.site import Site


def main() -> int:
    examples = Path(__file__).resolve().parent
    site = Site.load(str(examples / "site.example.yaml"))
    storage = _readable_storage(examples / "tiled-server.example.yml")
    server_root = str(site.readable[0].server)
    if server_root not in storage:
        raise SystemExit(f"server readable storage does not include {server_root}")
    sample = Path(str(site.readable[0].local)) / "sample.npy"
    payload = np.arange(4, dtype=np.float32)
    np.save(sample, payload)
    catalog = tempfile.TemporaryDirectory()
    server = SimpleTiledServer(directory=catalog.name, readable_storage=storage)
    try:
        client = from_uri(server.uri)
        binding = register(sample.as_uri(), client, context=BindContext(site=site))
        node = client
        for part in [part for part in binding.path.strip("/").split("/") if part]:
            node = node[part]
        read = np.asarray(node.read())
        if not np.array_equal(read, payload):
            raise SystemExit("read did not return the registered bytes")
        print(read.tobytes().hex())
        print(binding.path)
    finally:
        server.close()
        catalog.cleanup()
    return 0


def _readable_storage(config: Path) -> list[str]:
    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw = loaded["trees"][0]["args"]["readable_storage"]
    base = config.parent
    paths: list[str] = []
    for item in raw:
        path = Path(item)
        if not path.is_absolute():
            path = Path(os.path.normpath(base / path))
        paths.append(str(path))
    return paths


if __name__ == "__main__":
    raise SystemExit(main())
