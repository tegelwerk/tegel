"""Processed-measurement plugin. The server loads this module by entry point."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml

from tegel.core.contracts.resources import DataDescriptor, Resource, StructureFamily
from tegel.core.contracts.types import ResourceType, linkml_attribute_names
from tegel.core.plugins import Extractor, ProcessedDataPackage

SCHEMA_ID = "https://w3id.org/tegel/xfm/processed"
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schema" / "measurement.yaml"

RECORD = ResourceType(
    name="xfm.processed",
    version="1",
    metadata_schema=SCHEMA_ID,
)


class ProcessedExtractor(Extractor):
    """Claim a processed measurement record. Other files are left for another plugin."""

    @property
    def plugin_id(self) -> str:
        return "xfm-processed-v1"

    def validate(self, input_path: str) -> bool:
        body = _object(input_path)
        return (
            body is not None
            and isinstance(body.get("measurement_id"), str)
            and isinstance(body.get("sample_code"), str)
            and isinstance(body.get("key"), str)
            and body.get("cache") == "ProcessedCache"
        )

    def process(self, input_path: str, options: dict[str, Any]) -> ProcessedDataPackage:
        path = Path(input_path)
        body = _object(input_path)
        if body is None:
            raise ValueError("processed measurement record is not a JSON object")
        key = options.get("key")
        if isinstance(key, str) and key.strip():
            body["key"] = key
        body["payload_uri"] = _payload_uri(body, path)
        metadata: dict[str, Any] = {
            "plugin_id": self.plugin_id,
            "source_name": path.name,
            "resource_type": RECORD.name,
            "resource_type_version": RECORD.version,
        }
        for field in linkml_attribute_names(_schema()):
            metadata[field] = body[field]
        return ProcessedDataPackage(
            payload_path=str(path),
            metadata=metadata,
            metrics={},
        )


class ProcessedReader:
    """Describe the designated array from zarr metadata. Chunk files are not read."""

    @property
    def plugin_id(self) -> str:
        return "xfm-processed-v1"

    def validate(self, resource: Resource) -> bool:
        return resource.type == RECORD.name

    def describe(self, resource: Resource) -> DataDescriptor:
        uri = resource.metadata.get("payload_uri")
        if not isinstance(uri, str) or not uri:
            raise ValueError("payload_uri is required")
        store = _local_path(uri)
        array = _array_metadata(store)
        if array is None:
            return DataDescriptor(
                family=StructureFamily.bytes,
                media_type="application/octet-stream",
                content_url=f"{resource.uri}/data/content",
            )
        shape = [int(item) for item in array["shape"]]
        chunk = array["chunk_grid"]["configuration"]["chunk_shape"]
        return DataDescriptor(
            family=StructureFamily.array,
            media_type="application/zarr",
            shape=shape,
            chunks=[[int(item) for item in chunk]],
            data_type=str(array["data_type"]),
        )


def _schema() -> dict[str, Any]:
    loaded = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("measurement schema must be a YAML mapping")
    return loaded


def _object(input_path: str) -> dict[str, Any] | None:
    path = Path(input_path)
    if not path.is_file() or path.suffix.lower() != ".json":
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    return body


def _payload_uri(body: dict[str, Any], record: Path) -> str:
    explicit = body.get("payload_uri")
    if isinstance(explicit, str) and explicit.strip():
        return explicit
    designated = body.get("designated")
    if not isinstance(designated, str) or not designated.strip():
        return record.resolve().as_uri()
    base = record.parent
    crate = body.get("crate_path")
    if isinstance(crate, str) and crate not in {"", "."}:
        candidate = Path(crate)
        if not candidate.is_absolute():
            candidate = base / candidate
        if candidate.is_dir():
            base = candidate
    return (base / designated.strip().rstrip("/")).resolve().as_uri()


def _local_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    return Path(uri)


def _array_metadata(store: Path) -> dict[str, Any] | None:
    if store.is_file():
        return None
    preferred = store / "clr" / "zarr.json"
    paths = [preferred] if preferred.is_file() else []
    if store.is_dir():
        paths.extend(sorted(store.rglob("zarr.json")))
    seen: set[Path] = set()
    for path in paths:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        body = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(body, dict) and body.get("node_type") == "array" and "shape" in body:
            return body
    return None
