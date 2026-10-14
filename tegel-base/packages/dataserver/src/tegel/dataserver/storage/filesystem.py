"""Open catalog file URIs. This backend does not copy payloads."""

from __future__ import annotations

import os
from typing import BinaryIO
from urllib.parse import unquote, urlparse


class FileStorage:
    """Read bytes from a ``file`` URI the catalog already stored."""

    def open_bytes(self, uri: str) -> BinaryIO:
        return open(_path(uri), "rb")

    def open_range(self, uri: str, start: int, end: int) -> bytes:
        """Bytes ``start`` through ``end``, both inclusive."""
        if start < 0 or end < start:
            raise ValueError(f"invalid byte range {start}-{end}")
        with open(_path(uri), "rb") as handle:
            handle.seek(start)
            return handle.read(end - start + 1)

    def size(self, uri: str) -> int:
        return os.stat(_path(uri)).st_size


def _path(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("filesystem storage opens local file URIs")
    return unquote(parsed.path)
