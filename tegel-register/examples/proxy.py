#!/usr/bin/env python3
"""Proxy an upstream Tiled array or table.

Skips unless TEGEL_REGISTER_SITE, TEGEL_REGISTER_TILED_URI, and TEGEL_REGISTER_INTO are set.
Registration receives the upstream API key from TILED_UPSTREAM_API_KEY for
every secret id. The server process reads TEGEL_REGISTER_PROXY_CREDENTIALS, the
mode-0600 file written by ``tegel-register proxy credentials``. The client that
reads the node does not receive the upstream key.
"""
from __future__ import annotations

import os


def main() -> int:
    uri = os.environ.get("TEGEL_REGISTER_TILED_URI")
    into = os.environ.get("TEGEL_REGISTER_INTO")
    if not uri or not into or not os.environ.get("TEGEL_REGISTER_SITE"):
        print("skip: set TEGEL_REGISTER_SITE, TEGEL_REGISTER_TILED_URI, and TEGEL_REGISTER_INTO")
        return 0
    from tiled.client import from_uri

    from tegel.register import register
    from tegel.register.context import BindContext
    from tegel.register.site import Site

    class Secrets:
        def get_secret(self, secret_id: str):
            return {"api_key": os.environ.get("TILED_UPSTREAM_API_KEY", "")}

    binding = register(
        uri,
        from_uri(into),
        context=BindContext(site=Site.load(), secrets=Secrets()),
    )
    print(binding.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
