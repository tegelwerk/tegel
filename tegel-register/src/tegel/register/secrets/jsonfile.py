"""Local secrets manager: one mode-0600 JSON file per secret id.

Not registered as a default secrets provider, and not attached to a
``Context`` unless the caller passes it. A process that should not see the
files uses :class:`tegel.register.secrets.exchange.ExchangeSecrets` instead.
"""
from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

from tegel.register.errors import SecretLookupError

_SECRET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _write_private(path: Path, payload: str) -> None:
    """Create ``path`` at mode 0600 and write ``payload``. Retry once if it exists."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for attempt in range(2):
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError:
            if attempt == 1:
                raise
            path.unlink()
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return


def default_secrets_directory() -> Path:
    """Directory the local secrets manager uses when none is given."""
    return Path.home() / ".config" / "tegel-register" / "secrets"


class JsonFileSecrets:
    """``<directory>/<secret_id>.json`` as a flat string map."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    def get_secret(self, secret_id: str) -> Mapping[str, str]:
        path = self._directory / f"{secret_id}.json"
        try:
            mode = path.stat().st_mode
        except OSError as exc:
            raise SecretLookupError("secret file is not readable") from exc
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise SecretLookupError("secret file is group or world readable")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretLookupError("secret file is not readable") from exc
        if isinstance(loaded, (str, bytes)) or not isinstance(loaded, dict):
            raise SecretLookupError("secret file is not a string map")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in loaded.items()):
            raise SecretLookupError("secret file is not a string map")
        return {key: loaded[key] for key in loaded}

    def __repr__(self) -> str:
        return f"JsonFileSecrets({str(self._directory)!r})"


class LocalSecretsManager(JsonFileSecrets):
    """Store and read string maps. ``put_secret`` is the setup write.

    Files are mode ``0600``. The directory is mode ``0700``. Values are not
    included in errors or ``repr``.
    """

    @classmethod
    def default(cls) -> "LocalSecretsManager":
        return cls(default_secrets_directory())

    def put_secret(self, secret_id: str, secret: Mapping[str, str]) -> Path:
        if not isinstance(secret_id, str) or _SECRET_ID.fullmatch(secret_id) is None:
            raise SecretLookupError("secret id is not a single path-safe name")
        if isinstance(secret, (str, bytes)) or not isinstance(secret, Mapping):
            raise SecretLookupError("secret is not a string map")
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in secret.items()
        ):
            raise SecretLookupError("secret is not a string map")
        payload = json.dumps({key: secret[key] for key in secret})
        temporary = self._directory / f".{secret_id}.{os.getpid()}.tmp"
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            os.chmod(self._directory, 0o700)
            path = self._directory / f"{secret_id}.json"
            _write_private(temporary, payload)
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise SecretLookupError("secret could not be stored") from exc
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass
        return path

    def __repr__(self) -> str:
        return f"LocalSecretsManager({str(self._directory)!r})"
