"""Core source and specs stay free of modality vocabulary."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TERMS = (
    "crystal" + "lography",
    "beam" + "line",
    "space" + " group",
    "mtz",
    "xfm",
    "xrf",
)


def _files() -> list[Path]:
    found = [ROOT / "README.md"]
    prompts = (ROOT / "specs" / "prompts").resolve()
    for folder in ("src", "specs", "scripts"):
        found.extend(
            path
            for path in (ROOT / folder).rglob("*")
            if path.suffix in {".py", ".md"} and prompts not in path.resolve().parents
        )
    return found


def test_no_modality_terms() -> None:
    joined = "|".join(re.escape(term) for term in TERMS)
    phrase = re.compile(rf"\b(?:{joined})\b", re.IGNORECASE)
    token = re.compile(r"\bMX\b")
    failures: list[str] = []
    for file in _files():
        text = file.read_text(encoding="utf-8")
        match = phrase.search(text) or token.search(text)
        if match:
            failures.append(f"{file.relative_to(ROOT)}: {match.group(0)}")
    assert failures == []
