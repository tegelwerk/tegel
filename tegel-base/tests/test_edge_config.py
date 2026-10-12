"""The edge files name ports and paths only through edge.env."""

import re
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"


def test_compose_and_caddyfile_have_no_literal_ports_or_prefixes() -> None:
    banned_ports = re.compile(r"(?<![\w.])(8000|8080|4200)(?![\w.])")
    banned_paths = re.compile(r"(?<![\w/])/(?:data|auth|prefect)(?![\w])")
    for name in ("compose.yml", "Caddyfile"):
        text = (DEPLOY / name).read_text(encoding="utf-8")
        assert banned_ports.search(text) is None, name
        assert banned_paths.search(text) is None, name
