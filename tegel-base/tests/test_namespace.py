"""``tegel`` stays a namespace. Subpackages own their ``__init__`` files."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_namespace_has_no_parent_init() -> None:
    parents = [
        path
        for folder in ("packages", "examples")
        for path in (ROOT / folder).rglob("__init__.py")
        if path.parent.name == "tegel"
    ]
    assert parents == []
