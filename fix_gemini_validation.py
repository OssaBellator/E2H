"""One-use corrections for Gemini integration validation."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one correction anchor, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/e2h/gemini_generate_content.py",
    '                candidate_index = candidate.get("index", candidate_position)\n'
    "                message_id = record.candidate_ids[candidate_position]\n",
    "                message_id = record.candidate_ids[candidate_position]\n",
)

Path(__file__).unlink()
