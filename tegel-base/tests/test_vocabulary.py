"""Reference packages stay free of modality vocabulary."""

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


def test_packages_have_no_modality_terms() -> None:
    joined = "|".join(re.escape(term) for term in TERMS)
    phrase = re.compile(rf"\b(?:{joined})\b", re.IGNORECASE)
    token = re.compile(r"\bMX\b")
    failures: list[str] = []
    suffixes = {".py", ".md", ".yml", ".yaml", ".toml"}
    files = [path for path in (ROOT / "packages").rglob("*") if path.suffix in suffixes]
    for file in files:
        text = file.read_text(encoding="utf-8")
        match = phrase.search(text) or token.search(text)
        if match:
            failures.append(f"{file.relative_to(ROOT)}: {match.group(0)}")
        for name in ("tegel.mx", "tegel.xfm"):
            if name in text:
                failures.append(f"{file.relative_to(ROOT)}: {name}")
    assert failures == []
