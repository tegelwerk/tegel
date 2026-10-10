"""Toy resource type, extractor, and reader."""

from __future__ import annotations

import ast
import json
import struct
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from tegel.core.contracts.resources import DataDescriptor, Resource, StructureFamily
from tegel.core.contracts.types import ResourceType
from tegel.core.plugins import Extractor, ProcessedDataPackage

RECORD = ResourceType(
    name="toy.record",
    version="1",
    metadata_schema="https://w3id.org/tegel/toy/record",
)


class ToyExtractor(Extractor):
    """Claim a ``.npy`` file and read the sidecar next to it."""

    @property
    def plugin_id(self) -> str:
        return "toy-record"

    def validate(self, input_path: str) -> bool:
        return input_path.endswith(".npy")

    def process(self, input_path: str, options: dict[str, Any]) -> ProcessedDataPackage:
        path = Path(input_path)
        sidecar = path.with_suffix(".json")
        metadata: dict[str, Any] = {}
        if sidecar.is_file():
            loaded = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata.update(loaded)
        metadata["plugin_id"] = self.plugin_id
        metadata["source_name"] = path.name
        metadata["payload_uri"] = path.resolve().as_uri()
        metadata["resource_type"] = RECORD.name
        metadata["resource_type_version"] = RECORD.version
        key = options.get("key")
        if isinstance(key, str) and key:
            metadata["key"] = key
        elif "uuid" not in metadata:
            metadata["uuid"] = path.stem
        return ProcessedDataPackage(payload_path=str(path), metadata=metadata, metrics={})


class ToyReader:
    """Describe a toy record from the ``.npy`` header or as stored bytes.

    The header read stops before the array payload.
    """

    @property
    def plugin_id(self) -> str:
        return "toy-record"

    def validate(self, resource: Resource) -> bool:
        return resource.type == RECORD.name

    def describe(self, resource: Resource) -> DataDescriptor:
        uri = resource.metadata.get("payload_uri")
        if not isinstance(uri, str) or not uri:
            raise ValueError("payload_uri is required")
        path = Path(unquote(urlparse(uri).path))
        if path.suffix == ".npy":
            shape, data_type = _npy_header(path)
            return DataDescriptor(
                family=StructureFamily.array,
                media_type="application/x-npy",
                shape=shape,
                chunks=[shape],
                data_type=data_type,
            )
        return DataDescriptor(
            family=StructureFamily.bytes,
            media_type="application/octet-stream",
            content_url=f"{resource.uri}/data/content",
        )


def _npy_header(path: Path) -> tuple[list[int], str]:
    with path.open("rb") as handle:
        magic = handle.read(6)
        if magic != b"\x93NUMPY":
            raise ValueError("payload is not an npy file")
        major = handle.read(1)
        handle.read(1)
        if major == b"\x01":
            (length,) = struct.unpack("<H", handle.read(2))
        else:
            (length,) = struct.unpack("<I", handle.read(4))
        header = handle.read(length)
    parsed = ast.literal_eval(header.decode("latin1").strip())
    shape = [int(item) for item in parsed["shape"]]
    return shape, str(parsed["descr"])
