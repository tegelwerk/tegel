"""Write the action routes' OpenAPI document to tegel-core/specs/generated.

Only the routes this package adds are exported; the read API is the data server's.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI

from tegel.actionserver import __version__
from tegel.actionserver.routes import mount_action_routes
from tegel.core.version import SPEC_VERSION

SPECS = Path(__file__).resolve().parents[2] / "tegel-core/specs"
TARGET = SPECS / "generated/actionserver.openapi.json"


def main() -> None:
    app = FastAPI(title="Tegel action server", version=SPEC_VERSION,
                  description=f"Action routes of specs/actionserver.md "
                              f"(reference implementation tegel-actionserver {__version__}).")
    mount_action_routes(app)
    document = app.openapi()
    document["components"]["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
    document["security"] = [{"bearer": []}]
    TARGET.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {TARGET}")


if __name__ == "__main__":
    main()
