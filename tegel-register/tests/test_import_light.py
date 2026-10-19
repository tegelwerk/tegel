"""Import and bootstrap stay free of heavy libraries."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("TEGEL_REGISTER_SITE", None)
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def test_import_is_light(tmp_path):
    code = textwrap.dedent(
        """
        import sys
        import tegel.register
        heavy = ["tiled", "globus_sdk", "numpy", "pandas", "pyarrow"]
        loaded = [name for name in heavy if name in sys.modules]
        if loaded:
            raise SystemExit("loaded " + ",".join(loaded))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_bare_context_bootstraps(tmp_path):
    code = textwrap.dedent(
        """
        from tegel.register.context import BindContext
        binder = BindContext().binders.get("file")
        assert binder.protocol == "file"
        assert binder.api_version == 2
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
