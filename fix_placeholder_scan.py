"""Exclude complete E2H placeholders from residual detector input."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one placeholder anchor, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/e2h/privacy.py",
    "_ASSIGNMENT = re.compile(\n"
    "    r\"(?i)(\\b(?:api[_-]?key|access[_-]?token|secret|password)\\b\\s*[:=]\\s*[\\\"']?)\"\n"
    "    r\"([^\\s,\\\"']{8,})\"\n"
    ")\n",
    "_ASSIGNMENT = re.compile(\n"
    "    r\"(?i)(\\b(?:api[_-]?key|access[_-]?token|secret|password)\\b\\s*[:=]\\s*[\\\"']?)\"\n"
    "    r\"([^\\s,\\\"']{8,})\"\n"
    ")\n"
    "_REDACTION_PLACEHOLDER = re.compile(r\"<redacted:[^>\\r\\n]+>\")\n",
)
replace_once(
    "src/e2h/privacy.py",
    "def _candidate_matches(\n"
    "    value: str,\n"
    "    custom_patterns: list[tuple[CustomRedactionRule, re.Pattern[str]]],\n"
    ") -> Iterator[tuple[RedactionKind, str, str | None, str]]:\n"
    "    for match in _ASSIGNMENT.finditer(value):\n",
    "def _candidate_matches(\n"
    "    value: str,\n"
    "    custom_patterns: list[tuple[CustomRedactionRule, re.Pattern[str]]],\n"
    ") -> Iterator[tuple[RedactionKind, str, str | None, str]]:\n"
    "    scan_value = _REDACTION_PLACEHOLDER.sub(\"\", value)\n"
    "    for match in _ASSIGNMENT.finditer(scan_value):\n",
)
for old, new in (
    ("_BEARER.finditer(value)", "_BEARER.finditer(scan_value)"),
    ("pattern.finditer(value)", "pattern.finditer(scan_value)"),
    ("_EMAIL.finditer(value)", "_EMAIL.finditer(scan_value)"),
    ("_PHONE.finditer(value)", "_PHONE.finditer(scan_value)"),
):
    target = Path("src/e2h/privacy.py")
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count < 1:
        raise RuntimeError(f"missing residual scan anchor: {old}")
    target.write_text(text.replace(old, new), encoding="utf-8")

Path(__file__).unlink()
