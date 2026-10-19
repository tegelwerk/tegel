"""Adapters that forward array and table reads to an upstream Tiled server.

Loaded by a Tiled server config import string. This package does not import
binders, site configuration, or Globus.
"""
from __future__ import annotations

import re

from tiled.adapters.core import Adapter
from tiled.structures.array import ArrayStructure
from tiled.structures.core import StructureFamily
from tiled.structures.table import TableStructure

from tegel.register.redaction import redact_message
from tegel.register.tiled._compat import IncompatibleShapeError, NDSlice, init_adapter_from_catalog
from tegel.register.tiled_server.credentials import api_key_for, normalize_base

PROXY_ARRAY_MIMETYPE = "application/x-tegel-register-tiled-proxy;structure=array"
PROXY_TABLE_MIMETYPE = "application/x-tegel-register-tiled-proxy;structure=table"
PROXY_MIMETYPES = {"array": PROXY_ARRAY_MIMETYPE, "table": PROXY_TABLE_MIMETYPE}

_CLIENTS: dict[str, object] = {}


class RemoteArrayAdapter(Adapter[ArrayStructure]):
    """Serve an upstream array through this server."""

    structure_family = StructureFamily.array

    def __init__(self, data_uri, structure, *, path, metadata=None, specs=None):
        self._base = data_uri
        self._path = path
        super().__init__(structure, metadata=metadata, specs=specs)

    @classmethod
    def from_catalog(cls, data_source, node, /, **kwargs):
        kwargs.setdefault("path", dict(data_source.parameters or {}).get("path", ""))
        return init_adapter_from_catalog(cls, data_source, node, **kwargs)

    def read(self, slice=NDSlice()):
        node = self._node()
        return node[tuple(slice)] if slice else node.read()

    def read_block(self, block, slice=NDSlice()):
        array = self._node().read_block(tuple(block))
        return array[tuple(slice)] if slice else array

    def _node(self):
        node, api_key = _open(self._base)
        try:
            for part in str(self._path).strip("/").split("/"):
                if part:
                    node = node[part]
            _check_array(self.structure(), node.structure())
        except IncompatibleShapeError:
            raise
        except Exception as exc:
            raise RuntimeError(_scrub(exc, api_key)) from None
        return node


class RemoteTableAdapter(Adapter[TableStructure]):
    """Serve an upstream table through this server."""

    structure_family = StructureFamily.table

    def __init__(self, data_uri, structure, *, path, metadata=None, specs=None):
        self._base = data_uri
        self._path = path
        super().__init__(structure, metadata=metadata, specs=specs)

    @classmethod
    def from_catalog(cls, data_source, node, /, **kwargs):
        kwargs.setdefault("path", dict(data_source.parameters or {}).get("path", ""))
        return init_adapter_from_catalog(cls, data_source, node, **kwargs)

    def read(self, fields=None):
        node = self._node()
        return node.read(columns=fields) if fields else node.read()

    def read_partition(self, partition, fields=None):
        node = self._node()
        if fields:
            return node.read_partition(partition, columns=fields)
        return node.read_partition(partition)

    def _node(self):
        node, api_key = _open(self._base)
        try:
            for part in str(self._path).strip("/").split("/"):
                if part:
                    node = node[part]
            _check_table(self.structure(), node.structure())
        except IncompatibleShapeError:
            raise
        except Exception as exc:
            raise RuntimeError(_scrub(exc, api_key)) from None
        return node


def _open(base: str):
    """Return the cached upstream client and the API key used to open it."""
    key = normalize_base(base)
    cached = _CLIENTS.get(key)
    if cached is not None:
        return cached
    from tiled.client import from_uri

    api_key = None
    try:
        api_key = api_key_for(base)
        client = from_uri(key, api_key=api_key) if api_key else from_uri(key)
    except Exception as exc:
        raise RuntimeError(_scrub(exc, api_key)) from None
    _CLIENTS[key] = (client, api_key)
    return client, api_key


def _scrub(exc: BaseException, api_key: str | None) -> str:
    text = redact_message(str(exc))
    if api_key:
        text = text.replace(api_key, "<redacted>")
    return re.sub(r"(?i)([?&]api_key=)[^&\s'\"]+", r"\1<redacted>", text)


def _check_array(stored, remote) -> None:
    if tuple(stored.shape) != tuple(remote.shape) or stored.data_type != remote.data_type:
        raise IncompatibleShapeError("upstream array structure no longer matches")


def _check_table(stored, remote) -> None:
    if list(stored.columns) != list(remote.columns):
        raise IncompatibleShapeError("upstream table structure no longer matches")


def clear_client_cache() -> None:
    """Drop cached upstream clients. Tests use this between credential cases."""
    _CLIENTS.clear()
