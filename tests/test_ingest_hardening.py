from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.ingest import (
    EvidenceFormat,
    EvidenceIngestError,
    SourceProvenance,
    TranscriptDocument,
    import_otlp_data,
    import_transcript_document,
    ingest_transcript_file,
)

TRACE_ID = "0123456789abcdef0123456789abcdef"
SPAN_ID = "0123456789abcdef"
START_NS = 1_700_000_000_000_000_000


def provenance(source_format: EvidenceFormat, *, redact: bool = True) -> SourceProvenance:
    return SourceProvenance(
        format=source_format,
        source_name="source.json",
        sha256="0" * 64,
        size_bytes=1,
        redaction_enabled=redact,
    )


def span(*, trace_id: str = TRACE_ID, span_id: str = SPAN_ID) -> dict[str, Any]:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "agent.run",
        "startTimeUnixNano": str(START_NS),
        "endTimeUnixNano": str(START_NS + 1),
        "attributes": [],
        "events": [],
    }


def otlp(*spans: dict[str, Any]) -> dict[str, Any]:
    return {
        "resourceSpans": [
            {"scopeSpans": [{"spans": list(spans)}]},
        ]
    }


def transcript(*, trace_id: str = "conversation-1") -> TranscriptDocument:
    return TranscriptDocument.model_validate(
        {
            "id": trace_id,
            "metadata": {"source_format": "spoofed", "email": "alex@example.com"},
            "messages": [
                {
                    "id": "m1",
                    "role": "user",
                    "content": "contact alex@example.com",
                    "timestamp": "2026-08-05T12:00:00Z",
                }
            ],
        }
    )


def test_transcript_source_format_metadata_cannot_be_spoofed() -> None:
    bundle = import_transcript_document(
        transcript(),
        provenance(EvidenceFormat.TRANSCRIPT_JSON, redact=False),
    )
    assert bundle.traces[0].events[0].context.metadata["source_format"] == "transcript-json"


def test_redaction_locations_use_bundle_indexes_not_trace_ids() -> None:
    trace_id = "sk-abcdefghijklmnop"
    bundle = import_transcript_document(
        transcript(trace_id=trace_id),
        provenance(EvidenceFormat.TRANSCRIPT_JSON),
    )
    serialized_locations = "\n".join(record.location for record in bundle.redactions)
    assert trace_id not in serialized_locations
    assert all(
        location.startswith("/traces/0/events/")
        for location in serialized_locations.splitlines()
    )


def test_ingest_rejects_non_standard_json_constants(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        '{"id":"conversation-1","metadata":{"bad":NaN},"messages":['
        '{"id":"m1","role":"user","content":"hello",'
        '"timestamp":"2026-08-05T12:00:00Z"}]}',
        encoding="utf-8",
    )
    with pytest.raises(EvidenceIngestError, match="invalid evidence JSON"):
        ingest_transcript_file(path)


def test_transcript_metadata_rejects_non_finite_values() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        TranscriptDocument.model_validate(
            {
                "id": "conversation-1",
                "metadata": {"bad": math.nan},
                "messages": [
                    {
                        "id": "m1",
                        "role": "user",
                        "content": "hello",
                        "timestamp": "2026-08-05T12:00:00Z",
                    }
                ],
            }
        )


@pytest.mark.parametrize("value", [1.5, "1.5", 2**63, -(2**63) - 1])
def test_otlp_int_values_reject_lossy_or_out_of_range_values(value: object) -> None:
    raw_span = span()
    raw_span["attributes"] = [{"key": "bad", "value": {"intValue": value}}]
    with pytest.raises(EvidenceIngestError, match="integer"):
        import_otlp_data(otlp(raw_span), provenance(EvidenceFormat.OTLP_JSON, redact=False))


def test_otlp_double_values_must_be_finite() -> None:
    raw_span = span()
    raw_span["attributes"] = [{"key": "bad", "value": {"doubleValue": math.inf}}]
    with pytest.raises(EvidenceIngestError, match="JSON-serializable"):
        import_otlp_data(otlp(raw_span), provenance(EvidenceFormat.OTLP_JSON, redact=False))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("traceId", "0" * 32),
        ("spanId", "0" * 16),
        ("parentSpanId", "0" * 16),
    ],
)
def test_otlp_rejects_zero_identifiers(field: str, value: str) -> None:
    raw_span = span()
    raw_span[field] = value
    with pytest.raises(EvidenceIngestError, match="all zeros"):
        import_otlp_data(otlp(raw_span), provenance(EvidenceFormat.OTLP_JSON, redact=False))


def test_otlp_rejects_duplicate_span_ids_within_a_trace() -> None:
    with pytest.raises(EvidenceIngestError, match="duplicates"):
        import_otlp_data(
            otlp(span(), span()),
            provenance(EvidenceFormat.OTLP_JSON, redact=False),
        )
