"""Open catalog file URIs. This backend does not copy payloads."""

from __future__ import annotations

from typing import BinaryIO
from urllib.parse import unquote, urlparse


class FileStorage:
    """Read bytes from a ``file`` URI the catalog already stored."""

    def open_bytes(self, uri: str) -> BinaryIO:
        parsed = urlparse(uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ValueError("filesystem storage opens local file URIs")
        return open(unquote(parsed.path), "rb")
