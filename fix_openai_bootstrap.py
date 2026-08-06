"""One-use cleanup for the OpenAI Responses integration script."""

from pathlib import Path

path = Path("bootstrap_openai_responses.py")
text = path.read_text(encoding="utf-8")
old = '''replace_once(
    "README.md",
    "- [ ] Provider-specific transcript adapters.\\n",
    "- [x] OpenAI Responses API transcript adapter.\\n"
    "- [ ] Anthropic Messages API transcript adapter.\\n"
    "- [ ] Gemini GenerateContent transcript adapter.\\n",
) if False else None

'''
if text.count(old) != 1:
    raise RuntimeError("dead roadmap bootstrap expression anchor mismatch")
path.write_text(text.replace(old, "", 1), encoding="utf-8")
Path(__file__).unlink()
