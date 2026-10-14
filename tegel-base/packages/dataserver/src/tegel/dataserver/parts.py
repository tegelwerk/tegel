"""Content URLs for stored data parts, and HTTP byte ranges.

Content URLs are built per request from the configured origin and never stored.
"""

from __future__ import annotations

from dataclasses import dataclass

from tegel.core.contracts.resources import DataDescriptor, DataPart, Resource, StructureFamily
from tegel.core.upstreams import Upstream

TILED_PREFIX = "/api/v1"


def tiled_base(public_origin: str) -> str:
    """Tiled's API prefix under the public origin, which already carries the root path."""
    return public_origin.rstrip("/") + TILED_PREFIX


def tiled_route(family: StructureFamily) -> str:
    """The Tiled route that returns a whole node of ``family``."""
    if family is StructureFamily.array:
        return "array/full"
    if family is StructureFamily.table:
        return "table/full"
    return "node/full"


def part_content_url(
    part: DataPart,
    resource_id: str,
    *,
    public_origin: str,
    upstreams: dict[str, Upstream],
) -> str | None:
    """The one URL a client reads ``part`` from."""
    origin = public_origin.rstrip("/")
    if part.served_by == "tiled":
        return f"{tiled_base(origin)}/{tiled_route(part.family)}/{resource_id}/{part.name}"
    if part.served_by == "link":
        upstream = upstreams.get(part.upstream or "")
        if upstream is None or part.remote_path is None:
            return None
        return f"{upstream.base}/{tiled_route(part.family)}/{part.remote_path}"
    return f"{origin}/resources/{resource_id}/data/{part.name}/content"


def descriptor_for(
    resource: Resource,
    *,
    public_origin: str,
    upstreams: dict[str, Upstream],
) -> DataDescriptor:
    """The stored parts and payload of ``resource`` with content URLs filled in."""
    parts = [
        part.model_copy(
            update={
                "content_url": part_content_url(
                    part, resource.id, public_origin=public_origin, upstreams=upstreams
                )
            }
        )
        for part in resource.parts
    ]
    payload = None
    if resource.payload is not None:
        url = f"{public_origin.rstrip('/')}/resources/{resource.id}/data/content"
        payload = resource.payload.model_copy(update={"content_url": url})
    return DataDescriptor(parts=parts, payload=payload)


class RangeNotSatisfiable(ValueError):
    """The ``Range`` header cannot be served for this size."""


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def parse_range(header: str | None, size: int) -> ByteRange | None:
    """Parse one ``bytes=`` range. ``None`` means a full read.

    ``a-b``, ``a-`` and ``-n`` are served. Multiple ranges and anything
    unsatisfiable raise ``RangeNotSatisfiable``. Other units are ignored.
    """
    if header is None or not header.strip():
        return None
    unit, _, spec = header.strip().partition("=")
    if unit.strip().lower() != "bytes":
        return None
    spec = spec.strip()
    if "," in spec:
        raise RangeNotSatisfiable("multiple ranges are not supported")
    first, dash, last = spec.partition("-")
    first, last = first.strip(), last.strip()
    if not dash or not (first or last) or not (first + last).isdigit():
        raise RangeNotSatisfiable(f"invalid range {spec!r}")
    if not first:
        suffix = int(last)
        if suffix == 0 or size == 0:
            raise RangeNotSatisfiable("empty suffix range")
        return ByteRange(max(0, size - suffix), size - 1)
    start = int(first)
    end = size - 1 if not last else min(int(last), size - 1)
    if start >= size or (last and int(last) < start):
        raise RangeNotSatisfiable(f"range {spec} is outside {size} bytes")
    return ByteRange(start, end)
