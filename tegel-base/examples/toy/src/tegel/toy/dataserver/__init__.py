"""Toy resource type, extractor, and reader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from tegel.core.contracts.resources import DataDescriptor, DataPart, Resource, StructureFamily
from tegel.core.contracts.types import ResourceType
from tegel.core.plugins import Extractor, ProcessedDataPackage

RECORD = ResourceType(
    name="toy.record",
    version="1",
    metadata_schema="https://w3id.org/tegel/toy/record",
)
MARKER = "record.json"


class ToyExtractor(Extractor):
    """Claim a record directory through its ``record.json`` marker file."""

    @property
    def plugin_id(self) -> str:
        return "toy-record"

    def validate(self, input_path: str) -> bool:
        return Path(input_path).name == MARKER

    def process(self, input_path: str, options: dict[str, Any]) -> ProcessedDataPackage:
        path = Path(input_path)
        record: dict[str, Any] = {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            record.update(loaded)
        relations = record.pop("relations", None) or []
        if "uuid" not in record:
            record["uuid"] = path.parent.name
        return ProcessedDataPackage(
            payload_path=str(path),
            record=record,
            resource_type=RECORD.name,
            payload_uri=path.resolve().as_uri(),
            relations=relations,
            metrics={},
        )


class ToyReader:
    """List the parts present next to ``record.json``.

    ``frames.zarr`` is an array, ``stats.csv`` a table and ``notes.txt`` text.
    The array shape hint comes from ``zarr.json``; no chunk is opened.
    """

    @property
    def plugin_id(self) -> str:
        return "toy-record"

    def validate(self, resource: Resource) -> bool:
        return resource.type == RECORD.name

    def describe(self, resource: Resource) -> DataDescriptor:
        uri = resource.payload_uri
        if not uri:
            raise ValueError("payload_uri is required")
        folder = Path(unquote(urlparse(uri).path)).parent
        parts: list[DataPart] = []
        frames = folder / "frames.zarr"
        if (frames / "zarr.json").is_file():
            header = json.loads((frames / "zarr.json").read_text(encoding="utf-8"))
            parts.append(
                DataPart(
                    name="frames",
                    family=StructureFamily.array,
                    media_type="application/x-zarr",
                    asset_uri=frames.resolve().as_uri(),
                    designated=True,
                    title="Frames",
                    shape=[int(size) for size in header["shape"]],
                )
            )
        stats = folder / "stats.csv"
        if stats.is_file():
            parts.append(
                DataPart(
                    name="stats",
                    family=StructureFamily.table,
                    media_type="text/csv;header=present",
                    asset_uri=stats.resolve().as_uri(),
                    title="Statistics",
                )
            )
        notes = folder / "notes.txt"
        if notes.is_file():
            parts.append(
                DataPart(
                    name="notes",
                    family=StructureFamily.bytes,
                    media_type="text/plain",
                    asset_uri=notes.resolve().as_uri(),
                    title="Notes",
                )
            )
        payload = DataPart(
            name="payload",
            family=StructureFamily.bytes,
            media_type="application/json",
            asset_uri=uri,
        )
        return DataDescriptor(parts=parts, payload=payload)
