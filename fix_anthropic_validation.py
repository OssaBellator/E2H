"""One-use corrections for Anthropic validation ordering and test fixtures."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one correction anchor, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/e2h/anthropic_messages.py",
    "        for record in self.records:\n"
    "            if previous_timestamp is not None and record.timestamp < previous_timestamp:\n"
    "                raise ValueError(\"record timestamps must be nondecreasing\")\n"
    "            previous_timestamp = record.timestamp\n"
    "            response_id = cast(str, record.response[\"id\"])\n"
    "            if response_id in response_ids:\n"
    "                raise ValueError(\"response ids must be unique\")\n"
    "            response_ids.add(response_id)\n",
    "        for record in self.records:\n"
    "            response_id = cast(str, record.response[\"id\"])\n"
    "            if response_id in response_ids:\n"
    "                raise ValueError(\"response ids must be unique\")\n"
    "            response_ids.add(response_id)\n"
    "            if previous_timestamp is not None and record.timestamp < previous_timestamp:\n"
    "                raise ValueError(\"record timestamps must be nondecreasing\")\n"
    "            previous_timestamp = record.timestamp\n",
)
replace_once(
    "tests/test_anthropic_messages.py",
    '                        "content": first_response_content,\n',
    '                        "content": copy.deepcopy(first_response_content),\n',
)
replace_once(
    "tests/test_anthropic_messages.py",
    '    data["records"][0]["messages"][0]["content"] = "plain string"\n',
    '    data["records"][0]["messages"][0]["content"] = "plain string"\n'
    '    data["records"][1]["messages"][0]["content"] = "plain string"\n',
)

Path(__file__).unlink()
