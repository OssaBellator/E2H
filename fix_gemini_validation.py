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

path = Path("src/e2h/gemini_generate_content.py")
text = path.read_text(encoding="utf-8")
old_validation = '''                content = candidate.get("content")
                if not isinstance(content, dict):
                    continue
                candidate_parts = _parts(content.get("parts"), "candidate.content.parts")
'''
new_validation = '''                candidate_content = candidate.get("content")
                if not isinstance(candidate_content, dict):
                    continue
                candidate_parts = _parts(
                    candidate_content.get("parts"), "candidate.content.parts"
                )
'''
old_import = '''            content = candidate.get("content")
            if isinstance(content, dict):
                parts = _parts(content.get("parts"), "candidate.content.parts")
'''
new_import = '''            candidate_content = candidate.get("content")
            if isinstance(candidate_content, dict):
                parts = _parts(
                    candidate_content.get("parts"), "candidate.content.parts"
                )
'''
for old, new in ((old_validation, new_validation), (old_import, new_import)):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one candidate mapping anchor, found {count}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

Path(__file__).unlink()
