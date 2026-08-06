from __future__ import annotations

from datetime import UTC, datetime

import pytest

from e2h.gemini_generate_content import (
    GeminiContentRecord,
    GeminiGenerateContentDocument,
    import_gemini_generate_content_document,
)
from e2h.ingest import EvidenceFormat, SourceProvenance
from e2h.trace import TraceEventType


def test_function_arguments_and_results_preserve_nested_data_fields() -> None:
    args = {"data": {"customer_id": "CUS-123"}}
    response = {"data": {"status": "active"}}
    document = GeminiGenerateContentDocument.model_validate(
        {
            "id": "gemini-data",
            "records": [
                {
                    "timestamp": datetime(2026, 8, 6, 10, 0, tzinfo=UTC),
                    "candidate_ids": ["candidate-1"],
                    "response": {
                        "responseId": "response-1",
                        "candidates": [
                            {
                                "content": {
                                    "role": "model",
                                    "parts": [
                                        {
                                            "functionCall": {
                                                "id": "call-1",
                                                "name": "lookup_customer",
                                                "args": args,
                                            }
                                        },
                                        {
                                            "functionResponse": {
                                                "id": "call-1",
                                                "name": "lookup_customer",
                                                "response": response,
                                            }
                                        },
                                    ],
                                }
                            }
                        ],
                    },
                }
            ],
        }
    )
    provenance = SourceProvenance(
        format=EvidenceFormat.GEMINI_GENERATE_CONTENT_JSON,
        source_name="gemini.json",
        sha256="0" * 64,
        size_bytes=0,
        redaction_enabled=False,
    )

    bundle = import_gemini_generate_content_document(document, provenance)
    called = next(
        event for event in bundle.traces[0].events if event.event_type is TraceEventType.TOOL_CALLED
    )
    completed = next(
        event
        for event in bundle.traces[0].events
        if event.event_type is TraceEventType.TOOL_COMPLETED
    )

    assert called.payload["args"] == args
    assert completed.payload["response"] == response


def test_part_rejects_multiple_observable_payload_fields() -> None:
    with pytest.raises(ValueError, match="exactly one observable part field"):
        GeminiContentRecord(
            id="ambiguous-part",
            role="user",
            parts=[
                {
                    "text": "visible text",
                    "functionCall": {
                        "id": "call-1",
                        "name": "lookup_customer",
                        "args": {},
                    },
                }
            ],
        )


def test_part_rejects_duplicate_snake_and_camel_aliases() -> None:
    call = {"id": "call-1", "name": "lookup_customer", "args": {}}
    with pytest.raises(ValueError, match="must not define both function_call and functionCall"):
        GeminiContentRecord(
            id="duplicate-aliases",
            role="user",
            parts=[{"function_call": call, "functionCall": call}],
        )
