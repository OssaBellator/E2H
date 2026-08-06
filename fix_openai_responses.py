"""One-use corrections for OpenAI Responses ingestion validation."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one correction anchor, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/e2h/openai_responses.py",
    '        "output_item_ids": [item.get("id") for item in cast(list[dict[str, Any]], response["output"])],\n',
    '        "output_item_ids": [\n'
    '            item.get("id")\n'
    '            for item in cast(list[dict[str, Any]], response["output"])\n'
    '        ],\n',
)
replace_once(
    "tests/test_openai_responses.py",
    '        (lambda data: data["responses"].append(data["responses"][0]), "response ids must be unique"),\n',
    '        (\n'
    '            lambda data: data["responses"].append(data["responses"][0]),\n'
    '            "response ids must be unique",\n'
    '        ),\n',
)
replace_once(
    "tests/test_openai_responses_cli.py",
    '    assert len(traces.read_text(encoding="utf-8").splitlines()) == 4\n',
    '    assert len(traces.read_text(encoding="utf-8").splitlines()) == 1\n',
)

Path(__file__).unlink()
