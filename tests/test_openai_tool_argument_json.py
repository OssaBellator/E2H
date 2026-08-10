"""Regression coverage for strict derived OpenAI function-call argument JSON."""

from __future__ import annotations

import pytest

from e2h.ingest import EvidenceFormat, SourceProvenance
from e2h.openai_responses import OpenAIResponsesDocument, import_openai_responses_document
from e2h.trace import TraceEventType


def _document(arguments: str) -> OpenAIResponsesDocument:
    return OpenAIResponsesDocument.model_validate(
        {
            "schema_version": "0.1",
            "id": "tool-arguments",
            "responses": [
                {
                    "response": {
                        "id": "resp_1",
                        "object": "response",
                        "created_at": 1_786_000_000,
                        "model": "gpt-5.5",
                        "output": [
                            {
                                "id": "call_item",
                                "type": "function_call",
                                "call_id": "call_1",
                                "name": "lookup",
                                "arguments": arguments,
                            }
                        ],
                    }
                }
            ],
        }
    )


def _tool_payload(arguments: str) -> dict[str, object]:
    bundle = import_openai_responses_document(
        _document(arguments),
        SourceProvenance(
            format=EvidenceFormat.OPENAI_RESPONSES_JSON,
            source_name="responses.json",
            sha256="a" * 64,
            size_bytes=1,
            redaction_enabled=False,
        ),
    )
    event = next(
        item
        for item in bundle.traces[0].events
        if item.event_type is TraceEventType.TOOL_CALLED
    )
    return event.payload


def test_valid_tool_arguments_still_expose_structured_json() -> None:
    payload = _tool_payload('{"email":"alice@example.com"}')

    assert payload["arguments"] == '{"email":"alice@example.com"}'
    assert payload["arguments_json"] == {"email": "alice@example.com"}


@pytest.mark.parametrize(
    "arguments",
    [
        '{"email":"first","email":"second"}',
        '{"score":NaN}',
        '{"score":Infinity}',
    ],
)
def test_non_strict_tool_arguments_preserve_raw_text_without_derived_json(
    arguments: str,
) -> None:
    payload = _tool_payload(arguments)

    assert payload["arguments"] == arguments
    assert payload["arguments_json"] is None
