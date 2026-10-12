"""Write the operator credential file once, outside the git checkout."""

from __future__ import annotations

import os
import secrets
import stat
import sys
from pathlib import Path

NAMES = ("admin_password", "client_secret", "user_password", "session_secret")


def credential_path() -> Path:
    """Return the credentials file inside ``TEGEL_OUTPUT``."""
    output = os.environ.get("TEGEL_OUTPUT", "").strip()
    if not output:
        raise SystemExit("TEGEL_OUTPUT is required")
    return Path(output) / "credentials"


def ensure_credentials(path: Path) -> dict[str, str]:
    """Create the file when any required value is missing. Leave a complete file alone."""
    current = _read(path)
    if all(current.get(name) for name in NAMES):
        print(f"Using credentials in {path}", flush=True)
        return current
    generated = {name: secrets.token_urlsafe(18) for name in NAMES}
    generated["session_secret"] = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{name}={generated[name]}\n" for name in NAMES), encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"Wrote credentials to {path}", flush=True)
    return generated


def load_credentials(path: Path) -> dict[str, str]:
    """Read a complete credential file."""
    found = _read(path)
    missing = [name for name in NAMES if not found.get(name)]
    if missing:
        names = ", ".join(missing)
        raise SystemExit(f"{path} is missing {names}")
    return found


def _read(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        found[name.strip()] = value.strip()
    return found


def main() -> None:
    """Write credentials into ``TEGEL_OUTPUT`` when they are not already there."""
    ensure_credentials(credential_path())


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(exc, file=sys.stderr)
        raise
