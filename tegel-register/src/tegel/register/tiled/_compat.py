"""The only module that imports Tiled internals.

On import, the installed Tiled version must be at least 0.2.18 and below 0.3.
"""
from __future__ import annotations

from tegel.register.errors import PluginError

_SUPPORTED_MIN = (0, 2, 18)
_SUPPORTED_MAX = (0, 3, 0)


def _parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split(".")[:3]:
        digits = ""
        for char in piece:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _check_tiled_version() -> None:
    import tiled

    version = str(getattr(tiled, "__version__", "0"))
    parsed = _parse_version(version)
    if parsed < _SUPPORTED_MIN or parsed >= _SUPPORTED_MAX:
        raise PluginError(
            f"tiled {version} is installed; supported range is >=0.2.18,<0.3"
        )


_check_tiled_version()

from tiled.adapters.utils import IncompatibleShapeError, init_adapter_from_catalog  # noqa: E402
from tiled.client.register import resolve_mimetype, strip_suffixes  # noqa: E402
from tiled.utils import import_object  # noqa: E402
from tiled.mimetypes import (  # noqa: E402
    DEFAULT_MIMETYPES_BY_FILE_EXT,
    DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE,
)
from tiled.ndslice import NDSlice  # noqa: E402


def generate_data_sources(
    adapter, mimetype: str, path, is_directory: bool, dict_or_none, parameters=None
):
    """Call ``adapter.generate_data_sources`` with the signature the installed Tiled defines.

    Tiled 0.2.18 takes ``(mimetype, dict_or_none, item, is_directory)``. From 0.2.19 it takes
    ``(mimetype, item, is_directory, size=None, parameters=None)``, the way
    ``tiled.client.register.register_single_item`` calls it. ``parameters`` (an HDF5
    ``dataset``, say) reaches only the newer signature; 0.2.18 has no slot for it.
    """
    import inspect

    names = list(inspect.signature(adapter.generate_data_sources).parameters)
    if len(names) >= 2 and names[1] == "dict_or_none":
        return adapter.generate_data_sources(mimetype, dict_or_none, path, is_directory)
    size = None if is_directory else path.stat().st_size
    kwargs = {"size": size}
    if parameters:
        kwargs["parameters"] = parameters
    return adapter.generate_data_sources(mimetype, path, is_directory, **kwargs)


__all__ = [
    "generate_data_sources",
    "DEFAULT_MIMETYPES_BY_FILE_EXT",
    "DEFAULT_REGISTRATION_ADAPTERS_BY_MIMETYPE",
    "import_object",
    "IncompatibleShapeError",
    "NDSlice",
    "init_adapter_from_catalog",
    "resolve_mimetype",
    "strip_suffixes",
]
