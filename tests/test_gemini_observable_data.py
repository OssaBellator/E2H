from __future__ import annotations

from datetime import UTC, datetime

from e2h.gemini_generate_content import (
    GeminiGenerateContentDocument,
    import_gemini_generate_content_document,
)
from e2h.ingest import EvidenceFormat, SourceProvenance
from e2h.trace import TraceEventType


def test_function_arguments_preserve_nested_data_fields() -> None:
    args = {"data": {"customer_id": "CUS-123"}}
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
                                        }
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
        event
        for event in bundle.traces[0].events
        if event.event_type is TraceEventType.TOOL_CALLED
    )

    assert called.payload["args"] == args
