"""The server process holds upstream API keys in the mode-0600 file named by TEGEL_REGISTER_PROXY_CREDENTIALS.

Workers do not.
The file maps a normalized upstream base URI to ``{"api_key": "..."}`` and its
mode must be ``0600`` or stricter. A missing entry means the upstream is
anonymous. Keys are never logged.
"""
from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from urllib.parse import urlparse

from tegel.register.errors import SecretLookupError

ENV = "TEGEL_REGISTER_PROXY_CREDENTIALS"
_LOGGED: set[str] = set()
_LOGGER = logging.getLogger("tegel.register.tiled_server")


def normalize_base(uri: str) -> str:
    """Scheme, host, port, and path, with the trailing slash removed."""
    parsed = urlparse(uri)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{host}{path}"


def write_credentials(path: str | os.PathLike[str], base: str, api_key: str) -> Path:
    """Write or update one upstream key and leave the file at mode ``0600``.

    An existing file with a looser mode is refused. The key is not included in
    any error.
    """
    if not isinstance(api_key, str) or not api_key:
        raise SecretLookupError("proxy credential entry has no api_key")
    file = Path(path)
    payload: dict = {}
    if file.exists():
        payload = _load_mapping(file)
    payload[normalize_base(base)] = {"api_key": api_key}
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = file.with_name(file.name + ".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    except Exception:
        os.close(fd)
        temporary.unlink(missing_ok=True)
        raise
    os.close(fd)
    os.replace(temporary, file)
    os.chmod(file, 0o600)
    return file


def _load_mapping(file: Path) -> dict:
    try:
        mode = file.stat().st_mode
    except OSError as exc:
        raise SecretLookupError("proxy credential file is not readable") from exc
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        message = f"proxy credential file mode {mode & 0o777:03o} is too open"
        if str(file) not in _LOGGED:
            _LOGGED.add(str(file))
            _LOGGER.error(message)
        raise SecretLookupError(message)
    try:
        loaded = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecretLookupError("proxy credential file is not readable") from exc
    if not isinstance(loaded, dict):
        raise SecretLookupError("proxy credential file is not a mapping")
    return loaded


def api_key_for(base: str) -> str | None:
    """Return the API key for ``base``, or None when the upstream is anonymous."""
    path = os.environ.get(ENV)
    if not path:
        return None
    entry = _load_mapping(Path(path)).get(normalize_base(base))
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise SecretLookupError("proxy credential entry is not a mapping")
    api_key = entry.get("api_key")
    if api_key is None:
        return None
    if not isinstance(api_key, str) or not api_key:
        raise SecretLookupError("proxy credential entry has no api_key")
    return api_key
