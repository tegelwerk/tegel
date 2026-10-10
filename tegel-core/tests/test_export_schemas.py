"""Generated wire schemas match the export script."""

import json
from pathlib import Path

from scripts.export_schemas import GENERATED, model_schemas, openapi_document, write_schemas

ROOT = Path(__file__).resolve().parents[1]


def test_export_covers_the_read_api(tmp_path: Path) -> None:
    schemas = model_schemas()
    assert "Resource" in schemas
    assert "SearchProfile" in schemas
    assert "Capabilities" in schemas
    document = openapi_document(schemas)
    assert document["paths"]["/search"]["get"]["responses"]["200"]
    assert "/resources/{id}/data" in document["paths"]
    assert "post" not in json.dumps(document["paths"])
    write_schemas(tmp_path)
    committed = json.loads((GENERATED / "openapi.json").read_text(encoding="utf-8"))
    fresh = json.loads((tmp_path / "openapi.json").read_text(encoding="utf-8"))
    assert committed == fresh
    assert GENERATED == ROOT / "specs" / "generated"
