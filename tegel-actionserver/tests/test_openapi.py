"""The committed OpenAPI document matches the routes."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT.parent / "tegel-core/specs/generated/actionserver.openapi.json"


def test_openapi_document_is_current(tmp_path) -> None:
    before = DOCUMENT.read_text(encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "scripts/export_openapi.py")], check=True,
                   capture_output=True)
    after = DOCUMENT.read_text(encoding="utf-8")
    assert after == before, "run scripts/export_openapi.py and commit the result"
    paths = json.loads(after)["paths"]
    assert {"/actions", "/plans", "/runs", "/runs/{run_id}/wait", "/batches"} <= set(paths)
