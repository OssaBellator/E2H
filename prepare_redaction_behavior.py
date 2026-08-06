"""Normalize generated import placement before behavioral corrections."""

from pathlib import Path


path = Path("src/e2h/privacy.py")
text = path.read_text(encoding="utf-8")
old_counter = "from collections import Counter\n"
old_iterator = "from collections.abc import Iterator\nfrom typing import Any, Literal, cast\n"
if text.count(old_counter) != 1 or text.count(old_iterator) != 1:
    raise RuntimeError("privacy import anchors do not match the generated source")
text = text.replace(
    old_counter,
    "from collections import Counter\nfrom collections.abc import Iterator\n",
    1,
)
text = text.replace(old_iterator, "from typing import Any, Literal, cast\n", 1)
path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
