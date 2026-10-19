#!/usr/bin/env python3
"""Acquire one Zenodo file into the site landing area. Skips without TEGEL_REGISTER_ZENODO=1."""
from __future__ import annotations

import os


def main() -> int:
    if os.environ.get("TEGEL_REGISTER_ZENODO") != "1":
        print("skip: set TEGEL_REGISTER_ZENODO=1")
        return 0
    uri = os.environ.get("TEGEL_REGISTER_ZENODO_URI")
    into = os.environ.get("TEGEL_REGISTER_INTO")
    if not uri or not into or not os.environ.get("TEGEL_REGISTER_SITE"):
        print("skip: set TEGEL_REGISTER_SITE, TEGEL_REGISTER_ZENODO_URI, and TEGEL_REGISTER_INTO")
        return 0
    from tiled.client import from_uri

    from tegel.register import register
    from tegel.register.context import BindContext
    from tegel.register.site import Site

    binding = register(uri, from_uri(into), mode="acquire", context=BindContext(site=Site.load()))
    print(binding.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
