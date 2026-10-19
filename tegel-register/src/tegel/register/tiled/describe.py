"""Describe local bytes the way Tiled registers a file, with server path rewrite."""
from __future__ import annotations

from collections import ChainMap
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from tegel.register.errors import NotReadableError
from tegel.register.tiled._compat import (
    DEFAULT_MIMETYPES_BY_FILE_EXT,
    DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE,
    generate_data_sources,
    import_object,
    resolve_mimetype,
)

_PARQUET_MIMETYPE = "application/x-parquet"
_EXTRA_MIMETYPES = {".parquet": _PARQUET_MIMETYPE}


@dataclass(frozen=True)
class Description:
    structure_family: str
    mimetype: str
    data_sources: tuple[dict, ...]
    metadata: dict
    specs: tuple[dict, ...]


def describe_local(
    path: Path,
    *,
    server_uri: str,
    is_directory: bool,
    mimetypes_by_file_ext: Mapping[str, str] | None = None,
    adapters_by_mimetype: Mapping[str, Any] | None = None,
) -> Description | None:
    """Return a registration description, or None when the bytes are undescribable.

    Asset ``data_uri`` values are rewritten from the local path onto ``server_uri``.
    """
    mimetypes = ChainMap(
        dict(mimetypes_by_file_ext or {}),
        _EXTRA_MIMETYPES,
        DEFAULT_MIMETYPES_BY_FILE_EXT,
    )
    mimetype = resolve_mimetype(path, mimetypes)
    if mimetype is None:
        return None
    adapters = ChainMap(dict(adapters_by_mimetype or {}), DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE)
    try:
        adapter_cls = adapters[mimetype]
    except KeyError:
        return None
    if isinstance(adapter_cls, str):
        adapter_cls = import_object(adapter_cls)
    if not hasattr(adapter_cls, "from_uris"):
        if mimetype != _PARQUET_MIMETYPE:
            return None
        rewritten = (_rewrite_source(_parquet_source(path, mimetype), path.as_uri(), server_uri),)
        return Description(
            structure_family="table",
            mimetype=mimetype,
            data_sources=rewritten,
            metadata={},
            specs=(),
        )
    adapter = adapter_cls.from_uris(path.as_uri())
    if hasattr(adapter, "generate_data_sources"):
        data_sources = generate_data_sources(adapter, mimetype, path, is_directory, _dict_or_none)
    else:
        data_sources = [_single_asset(adapter, mimetype, path, is_directory)]
    rewritten = tuple(
        _rewrite_source(source, path.as_uri(), server_uri) for source in data_sources
    )
    specs = []
    for spec in adapter.specs or []:
        if isinstance(spec, dict):
            specs.append(dict(spec))
        else:
            specs.append(asdict(spec))
    family = adapter.structure_family
    family_value = family.value if isinstance(family, Enum) else str(family)
    return Description(
        structure_family=family_value,
        mimetype=mimetype,
        data_sources=rewritten,
        metadata=dict(adapter.metadata() or {}),
        specs=tuple(specs),
    )


def _dict_or_none(structure: Any) -> dict | None:
    if structure is None:
        return None
    return _plain(asdict(structure))


def _parquet_source(path: Path, mimetype: str) -> Any:
    """One parquet file. Tiled's parquet adapter has no ``from_uris``."""
    import pandas as pd
    from tiled.structures.core import StructureFamily
    from tiled.structures.data_source import Asset, DataSource, Management
    from tiled.structures.table import TableStructure

    frame = pd.read_parquet(path)
    return DataSource(
        structure_family=StructureFamily.table,
        mimetype=mimetype,
        structure=_dict_or_none(TableStructure.from_pandas(frame)),
        parameters={},
        management=Management.external,
        assets=[
            Asset(
                data_uri=path.as_uri(),
                is_directory=False,
                size=path.stat().st_size,
                parameter="data_uris",
                num=0,
            )
        ],
    )


def _single_asset(adapter: Any, mimetype: str, path: Path, is_directory: bool) -> Any:
    from tiled.structures.data_source import Asset, DataSource, Management

    family = adapter.structure_family
    return DataSource(
        structure_family=family,
        mimetype=mimetype,
        structure=_dict_or_none(adapter.structure()),
        parameters={},
        management=Management.external,
        assets=[
            Asset(
                data_uri=path.as_uri(),
                is_directory=is_directory,
                size=path.stat().st_size if not is_directory else None,
                parameter="data_uri",
            )
        ],
    )


def _rewrite_source(source: Any, local_uri: str, server_uri: str) -> dict:
    raw = _plain(asdict(source) if not isinstance(source, dict) else source)
    assets = []
    for asset in raw.get("assets") or []:
        updated = dict(asset)
        updated["data_uri"] = _rewrite_uri(str(asset["data_uri"]), local_uri, server_uri)
        assets.append(updated)
    raw["assets"] = assets
    return raw


def _rewrite_uri(data_uri: str, local_uri: str, server_uri: str) -> str:
    """Replace the local file URI with the server URI, ignoring a localhost authority."""
    if urlparse(data_uri).scheme != "file":
        return data_uri
    local_path = _uri_path(local_uri)
    asset_path = _uri_path(data_uri)
    server_path = _uri_path(server_uri)
    if asset_path == local_path:
        rewritten = server_path
    else:
        try:
            relative = asset_path.relative_to(local_path)
        except ValueError as exc:
            raise NotReadableError(f"asset {data_uri} is not under {local_uri}") from exc
        rewritten = server_path / relative
    rewritten_uri = Path(rewritten).as_uri()
    if not _under_server(rewritten_uri, server_uri):
        raise NotReadableError(f"rewritten asset {rewritten_uri} is outside {server_uri}")
    return rewritten_uri


def _under_server(rewritten: str, server_uri: str) -> bool:
    rewritten_path = _uri_path(rewritten)
    server_path = _uri_path(server_uri)
    if rewritten_path == server_path:
        return True
    try:
        rewritten_path.relative_to(server_path)
        return True
    except ValueError:
        return False


def _uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    return Path(url2pathname(parsed.path))


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value
