"""Package layout for one dataserver incarnation. No server imports."""

from __future__ import annotations

import re
from pathlib import Path

_TREE_WALK = re.compile(r"(?:\.rglob\s*\(|\bos\.walk\s*\(|\.walk\s*\()")


def assert_incarnation_layout(package_root: Path | str) -> None:
    """Fail when the dataserver package is not one modality module tree.

    Python under ``src/`` must live in ``src/tegel/<modality>/dataserver/``.
    ``src/tegel/__init__.py`` is forbidden, and those modules must not walk a tree.
    """
    src = Path(package_root) / "src"
    if not src.is_dir():
        raise AssertionError("src/ is missing")
    tegel = src / "tegel"
    if (tegel / "__init__.py").is_file():
        raise AssertionError("src/tegel/__init__.py makes tegel a regular package")
    modules = [path for path in src.rglob("*.py") if "__pycache__" not in path.parts]
    outside = [path for path in modules if not _inside_dataserver(path, src)]
    if outside:
        names = ", ".join(path.relative_to(src).as_posix() for path in outside)
        raise AssertionError(f"Python module outside src/tegel/<modality>/dataserver: {names}")
    modalities = {_modality(path, src) for path in modules}
    if len(modalities) != 1:
        raise AssertionError("src/tegel must contain one modality dataserver package")
    for path in modules:
        text = path.read_text(encoding="utf-8")
        if _TREE_WALK.search(text):
            name = path.relative_to(src).as_posix()
            raise AssertionError(f"{name} walks a tree")


def _inside_dataserver(path: Path, src: Path) -> bool:
    parts = path.relative_to(src).parts
    return len(parts) >= 4 and parts[0] == "tegel" and parts[2] == "dataserver"


def _modality(path: Path, src: Path) -> str:
    return path.relative_to(src).parts[1]
