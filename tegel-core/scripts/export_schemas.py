"""Write JSON Schema and OpenAPI for the wire contracts.

These files describe the pydantic models. Resource types are authored as
LinkML, not as these schemas.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tegel.core.contracts.capabilities import Capabilities, InstalledPlugin, Principal  # noqa: E402
from tegel.core.contracts.provenance import Provenance  # noqa: E402
from tegel.core.contracts.resources import (  # noqa: E402
    DataDescriptor,
    ErrorBody,
    Relation,
    Resource,
    ResourcePage,
    ResourceRef,
    SearchHit,
    SearchQuery,
    SearchResult,
)
from tegel.core.contracts.types import ResourceType, SearchProfile  # noqa: E402
from tegel.core.plugins import ProcessedDataPackage  # noqa: E402
from tegel.core.version import SPEC_VERSION  # noqa: E402

MODELS = (
    Capabilities,
    InstalledPlugin,
    Principal,
    Provenance,
    DataDescriptor,
    ErrorBody,
    Relation,
    Resource,
    ResourcePage,
    ResourceRef,
    SearchHit,
    SearchQuery,
    SearchResult,
    ResourceType,
    SearchProfile,
    ProcessedDataPackage,
)

GENERATED = ROOT / "specs" / "generated"


def model_schemas() -> dict[str, dict]:
    """JSON Schema for each wire model, keyed by class name."""
    return {model.__name__: model.model_json_schema() for model in MODELS}


def openapi_document(schemas: dict[str, dict]) -> dict:
    """OpenAPI 3.1 document for the read-only data API."""
    json_content = {
        "application/json": {"schema": {"$ref": "#/components/schemas/SearchResult"}}
    }
    crate_content = {
        "application/ld+json": {
            "schema": {"type": "object", "description": "RO-Crate 1.1 search document"}
        }
    }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Tegel data server",
            "version": SPEC_VERSION,
            "description": (
                "Read-only catalog API. The reference search document is an RO-Crate. "
                "A server may advertise another search_document and return SearchResult JSON."
            ),
        },
        "paths": {
            "/capabilities": {
                "get": _op("Capabilities", "Server version, plugins, search document, and auth")
            },
            "/search": {
                "get": {
                    "summary": "Search the catalog",
                    "parameters": [
                        {
                            "name": "field",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": "Profile search field. Other fields use their names.",
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "RO-Crate, or SearchResult for another search_document",
                            "content": {**crate_content, **json_content},
                        },
                        "400": _error("Unknown search field"),
                        "401": _error("Authentication required"),
                    },
                }
            },
            "/resources": {"get": _op("ResourcePage", "List catalog resources")},
            "/resources/{id}": {"get": _op("Resource", "One catalog resource", resource_id=True)},
            "/resources/{id}/metadata": {
                "get": {
                    "summary": "Catalog metadata object",
                    "parameters": [_id_param()],
                    "responses": {
                        "200": {
                            "description": "Metadata as stored",
                            "content": {
                                "application/json": {"schema": {"type": "object"}}
                            },
                        },
                        "401": _error("Authentication required"),
                        "404": _error("Resource not found"),
                    },
                }
            },
            "/resources/{id}/data": {
                "get": _op("DataDescriptor", "Structure-family descriptor", resource_id=True)
            },
            "/resources/{id}/data/content": {
                "get": {
                    "summary": "Stored bytes when the descriptor family is bytes",
                    "parameters": [_id_param()],
                    "responses": {
                        "200": {
                            "description": "Payload bytes",
                            "content": {
                                "application/octet-stream": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        },
                        "401": _error("Authentication required"),
                        "404": _error("Resource not found"),
                    },
                }
            },
        },
        "components": {"schemas": schemas},
    }


def _id_param() -> dict:
    return {
        "name": "id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }


def _error(description: str) -> dict:
    return {
        "description": description,
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/ErrorBody"}}
        },
    }


def _op(schema_name: str, summary: str, *, resource_id: bool = False) -> dict:
    parameters = [_id_param()] if resource_id else []
    return {
        "summary": summary,
        "parameters": parameters,
        "responses": {
            "200": {
                "description": summary,
                "content": {
                    "application/json": {
                        "schema": {"$ref": f"#/components/schemas/{schema_name}"}
                    }
                },
            },
            "401": _error("Authentication required"),
            **({"404": _error("Resource not found")} if resource_id else {}),
        },
    }


def write_schemas(directory: Path | None = None) -> Path:
    """Write ``wire.schema.json`` and ``openapi.json``."""
    target = directory or GENERATED
    target.mkdir(parents=True, exist_ok=True)
    schemas = model_schemas()
    (target / "wire.schema.json").write_text(
        json.dumps(schemas, indent=2) + "\n",
        encoding="utf-8",
    )
    (target / "openapi.json").write_text(
        json.dumps(openapi_document(schemas), indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def main() -> int:
    path = write_schemas()
    print(f"wrote {path / 'wire.schema.json'}")
    print(f"wrote {path / 'openapi.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
