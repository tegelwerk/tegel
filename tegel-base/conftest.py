"""Every test reads LinkML schemas from the example schema root."""

from pathlib import Path

import pytest

SCHEMA_ROOT = Path(__file__).resolve().parent / "examples" / "schemas"


@pytest.fixture(autouse=True)
def _schema_root(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TEGEL_SCHEMA_ROOT", str(SCHEMA_ROOT))
    return SCHEMA_ROOT
