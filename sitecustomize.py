"""One-use bootstrap patch for strict collection typing."""

from pathlib import Path

path = Path("src/e2h/compiler.py")
text = path.read_text(encoding="utf-8")
replacements = (
    (
        '        candidates = [event for event in messages if event.attributes.get("role") == "user"]\n'
        "        if not candidates:\n",
        '        user_messages = [event for event in messages if event.attributes.get("role") == "user"]\n'
        "        if not user_messages:\n",
    ),
    (
        "        selected = max(\n            candidates,\n",
        "        selected = max(\n            user_messages,\n",
    ),
    (
        "    candidates: list[tuple[TraceEvent, TraceEvent, TraceEvent | None]] = []\n",
        "    correction_candidates: list[tuple[TraceEvent, TraceEvent, TraceEvent | None]] = []\n",
    ),
    (
        "        candidates.append((correction_message, corrected_message, feedback))\n",
        "        correction_candidates.append((correction_message, corrected_message, feedback))\n",
    ),
    (
        "        candidates,\n        key=lambda item: (item[0].timestamp, item[0].trace_id, item[0].sequence),\n",
        "        correction_candidates,\n"
        "        key=lambda item: (item[0].timestamp, item[0].trace_id, item[0].sequence),\n",
    ),
)
for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"compiler typing bootstrap anchor mismatch: expected 1, found {count}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
