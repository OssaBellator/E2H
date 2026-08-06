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
indexer = '''def _index_function_calls(
    document: OpenAIResponsesDocument,
) -> dict[str, dict[str, Any]]:
    """Index observable function calls before emitting any output events."""
    calls: dict[str, dict[str, Any]] = {}
    for record in document.responses:
        output = cast(list[dict[str, Any]], record.response["output"])
        for item in [*record.input_items, *output]:
            if item.get("type") != "function_call":
                continue
            call_id = item.get("call_id")
            name = item.get("name")
            if not isinstance(call_id, str) or not call_id:
                continue
            if not isinstance(name, str) or not name:
                continue
            metadata = {
                "name": name,
                "namespace": item.get("namespace"),
                "provider_item_id": item.get("id"),
            }
            existing = calls.get(call_id)
            if existing is not None and existing != metadata:
                raise EvidenceIngestError(
                    f"function call {call_id!r} has conflicting provider metadata"
                )
            calls[call_id] = metadata
    return calls


'''
replace_once(
    "src/e2h/openai_responses.py",
    "def import_openai_responses_document(\n",
    indexer + "def import_openai_responses_document(\n",
)
replace_once(
    "src/e2h/openai_responses.py",
    "    seen_items: set[str] = set()\n"
    "    calls: dict[str, dict[str, Any]] = {}\n"
    "    for record in document.responses:\n",
    "    seen_items: set[str] = set()\n"
    "    calls = _index_function_calls(document)\n"
    "    for record in document.responses:\n",
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
    "tests/test_openai_responses.py",
    '''        (
            lambda data: data["responses"].append(
                {
                    "input_items": [],
                    "response": response("resp_earlier", 1, []),
                }
            ),
            "timestamps must be nondecreasing",
        ),
''',
    '''        (
            lambda data: data["responses"][1]["response"].update(
                {"created_at": 1}
            ),
            "timestamps must be nondecreasing",
        ),
''',
)
replace_once(
    "tests/test_openai_responses.py",
    '    assert "[REDACTED_EMAIL]" in rendered\n',
    '    assert "<redacted:email:" in rendered\n',
)
replace_once(
    "tests/test_openai_responses.py",
    '    assert "[REDACTED_SECRET]" in rendered\n',
    '    assert "<redacted:secret:" in rendered\n',
)
replace_once(
    "tests/test_openai_responses.py",
    '    with pytest.raises(EvidenceIngestError, match="invalid JSON"):\n',
    '    with pytest.raises(EvidenceIngestError, match="invalid evidence JSON"):\n',
)
replace_once(
    "tests/test_openai_responses.py",
    '''    with pytest.raises(EvidenceIngestError, match="invalid evidence JSON"):
        ingest_openai_responses_file(path)
''',
    '''    with pytest.raises(EvidenceIngestError, match="invalid evidence JSON"):
        ingest_openai_responses_file(path)


def test_conflicting_function_call_metadata_is_rejected() -> None:
    data = export_document()
    second_call = dict(data["responses"][1]["input_items"][1])
    second_call["name"] = "different_tool"
    data["responses"][1]["input_items"][1] = second_call
    document = OpenAIResponsesDocument.model_validate(data)
    with pytest.raises(EvidenceIngestError, match="conflicting provider metadata"):
        import_openai_responses_document(document, provenance())
''',
)
replace_once(
    "tests/test_openai_responses_cli.py",
    '    assert "[REDACTED_EMAIL]" in result.stdout\n',
    '    assert "<redacted:email:" in result.stdout\n',
)
replace_once(
    "tests/test_openai_responses_cli.py",
    '    assert len(traces.read_text(encoding="utf-8").splitlines()) == 1\n',
    '    assert len(traces.read_text(encoding="utf-8").splitlines()) == len(\n'
    '        payload["traces"][0]["events"]\n'
    '    )\n',
)

Path(__file__).unlink()
