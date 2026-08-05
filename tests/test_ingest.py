from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.ingest import (
    EvidenceFormat,
    EvidenceIngestError,
    RedactionKind,
    SourceProvenance,
    TranscriptDocument,
    TranscriptMessage,
    import_otlp_data,
    import_transcript_document,
    ingest_otlp_file,
    ingest_transcript_file,
)
from e2h.trace import TraceEventType

TRACE_ID = "0123456789abcdef0123456789abcdef"
SPAN_ID = "0123456789abcdef"
START_NS = 1_700_000_000_000_000_000


def provenance(
    source_format: EvidenceFormat,
    *,
    redact: bool = True,
) -> SourceProvenance:
    return SourceProvenance(
        format=source_format,
        source_name="source.json",
        sha256="0" * 64,
        size_bytes=1,
        redaction_enabled=redact,
    )


def transcript_data() -> dict[str, Any]:
    return {
        "id": "conversation-1",
        "capsule_id": "capsule-1",
        "metadata": {"owner": "alex@example.com"},
        "messages": [
            {
                "id": "m1",
                "role": "user",
                "content": (
                    "Use token ghp_abcdefghijklmnopqrstuvwxyz123456 "
                    "and call +61 412 345 678."
                ),
                "timestamp": "2026-08-05T12:00:00Z",
            },
            {
                "id": "m2",
                "role": "assistant",
                "content": "Bearer abcdefghijklmnopqrstuvwxyz and password=correct-horse-battery",
                "timestamp": "2026-08-05T12:00:01Z",
                "metadata": {"email-key@example.com": "sk-abcdefghijklmnop"},
            },
            {
                "id": "m3",
                "role": "user",
                "content": "That answer is incorrect.",
                "timestamp": "2026-08-05T12:00:02Z",
                "correction_of": "m2",
            },
        ],
    }


def otlp_span(
    *,
    trace_id: str = TRACE_ID,
    span_id: str = SPAN_ID,
    parent_span_id: str | None = None,
    start_ns: int = START_NS,
    end_ns: int = START_NS + 1_000_000_000,
) -> dict[str, Any]:
    span: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": "agent.run",
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": [
            {"key": "string", "value": {"stringValue": "alex@example.com"}},
            {"key": "boolean", "value": {"boolValue": True}},
            {"key": "integer", "value": {"intValue": "7"}},
            {"key": "double", "value": {"doubleValue": 2.5}},
            {"key": "bytes", "value": {"bytesValue": "YWJj"}},
            {
                "key": "array",
                "value": {
                    "arrayValue": {
                        "values": [
                            {"stringValue": "one"},
                            {"intValue": "2"},
                        ]
                    }
                },
            },
            {
                "key": "mapping",
                "value": {
                    "kvlistValue": {
                        "values": [
                            {"key": "nested", "value": {"stringValue": "value"}}
                        ]
                    }
                },
            },
        ],
        "events": [
            {
                "timeUnixNano": str(start_ns + 500_000_000),
                "name": "checkpoint",
                "attributes": [
                    {"key": "api_key", "value": {"stringValue": "sk-abcdefghijklmnop"}}
                ],
            }
        ],
        "status": {"code": 1, "message": "ok"},
    }
    if parent_span_id is not None:
        span["parentSpanId"] = parent_span_id
    return span


def otlp_data(*spans: dict[str, Any]) -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "schemaUrl": "https://example.test/resource",
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "e2h-test"},
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "schemaUrl": "https://example.test/scope",
                        "scope": {
                            "name": "e2h.tests",
                            "version": "1.0",
                            "attributes": [
                                {"key": "scope.flag", "value": {"boolValue": True}}
                            ],
                        },
                        "spans": list(spans),
                    }
                ],
            }
        ]
    }


def test_transcript_import_captures_corrections_and_redacts() -> None:
    document = TranscriptDocument.model_validate(transcript_data())
    bundle = import_transcript_document(
        document,
        provenance(EvidenceFormat.TRANSCRIPT_JSON),
        capsule_id="override-capsule",
    )

    trace = bundle.traces[0]
    assert trace.trace_id == "conversation-1"
    assert trace.events[0].context.capsule_id == "override-capsule"
    assert [event.event_type for event in trace.events] == [
        TraceEventType.CONVERSATION_STARTED,
        TraceEventType.MESSAGE_OBSERVED,
        TraceEventType.MESSAGE_OBSERVED,
        TraceEventType.MESSAGE_OBSERVED,
        TraceEventType.FEEDBACK_OBSERVED,
    ]
    assert bundle.corrections[0].correction_of == "m2"
    assert bundle.corrections[0].event_sequence == 4
    serialized = bundle.model_dump_json()
    for secret in (
        "alex@example.com",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "+61 412 345 678",
        "abcdefghijklmnopqrstuvwxyz",
        "correct-horse-battery",
        "sk-abcdefghijklmnop",
        "email-key@example.com",
    ):
        assert secret not in serialized
    assert {record.kind for record in bundle.redactions} == {
        RedactionKind.SECRET,
        RedactionKind.EMAIL,
        RedactionKind.PHONE,
    }
    assert all(record.placeholder.startswith("<redacted:") for record in bundle.redactions)


def test_redaction_placeholders_are_stable() -> None:
    document = TranscriptDocument.model_validate(transcript_data())
    first = import_transcript_document(
        document, provenance(EvidenceFormat.TRANSCRIPT_JSON)
    )
    second = import_transcript_document(
        document, provenance(EvidenceFormat.TRANSCRIPT_JSON)
    )
    assert [record.placeholder for record in first.redactions] == [
        record.placeholder for record in second.redactions
    ]
    assert [record.digest for record in first.redactions] == [
        record.digest for record in second.redactions
    ]


def test_transcript_import_can_preserve_raw_values() -> None:
    document = TranscriptDocument.model_validate(transcript_data())
    bundle = import_transcript_document(
        document,
        provenance(EvidenceFormat.TRANSCRIPT_JSON, redact=False),
    )
    assert bundle.redactions == []
    assert "alex@example.com" in bundle.model_dump_json()


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda data: data["messages"].append(data["messages"][0].copy()), "unique"),
        (
            lambda data: data["messages"].__setitem__(
                1, {**data["messages"][1], "timestamp": "2026-08-05T11:59:59Z"}
            ),
            "nondecreasing",
        ),
        (
            lambda data: data["messages"][0].update({"correction_of": "missing"}),
            "earlier",
        ),
        (
            lambda data: data["messages"][2].update({"correction_of": "m1"}),
            "assistant",
        ),
        (
            lambda data: data["messages"][2].update({"role": "assistant"}),
            "user messages",
        ),
    ],
)
def test_transcript_rejects_invalid_message_relationships(
    mutator: Any,
    message: str,
) -> None:
    data = transcript_data()
    mutator(data)
    with pytest.raises(ValidationError, match=message):
        TranscriptDocument.model_validate(data)


def test_transcript_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        TranscriptMessage(
            id="m1",
            role="user",
            content="hello",
            timestamp=datetime(2026, 8, 5),
        )


def test_transcript_rejects_non_json_metadata() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        TranscriptMessage(
            id="m1",
            role="user",
            content="hello",
            timestamp=datetime.now(UTC),
            metadata={"bad": object()},
        )
    data = transcript_data()
    data["metadata"] = {"bad": object()}
    with pytest.raises(ValidationError, match="JSON-serializable"):
        TranscriptDocument.model_validate(data)


def test_ingest_transcript_file_records_content_hash(tmp_path: Path) -> None:
    path = tmp_path / "conversation.json"
    raw = json.dumps(transcript_data()).encode()
    path.write_bytes(raw)
    bundle = ingest_transcript_file(path)
    assert bundle.provenance.source_name == "conversation.json"
    assert bundle.provenance.sha256 == hashlib.sha256(raw).hexdigest()
    assert bundle.provenance.size_bytes == len(raw)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (b"\xff", "UTF-8"),
        (b"{", "invalid evidence JSON"),
        (b"[]", "root must be an object"),
        (b'{"id":"bad"}', "messages"),
    ],
)
def test_ingest_transcript_file_wraps_invalid_sources(
    tmp_path: Path,
    contents: bytes,
    message: str,
) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(contents)
    with pytest.raises(EvidenceIngestError, match=message):
        ingest_transcript_file(path)


def test_ingest_file_wraps_missing_and_oversized_sources(tmp_path: Path) -> None:
    with pytest.raises(EvidenceIngestError, match="unable to read"):
        ingest_transcript_file(tmp_path / "missing.json")
    oversized = tmp_path / "large.json"
    oversized.write_bytes(b" " * (10 * 1024 * 1024 + 1))
    with pytest.raises(EvidenceIngestError, match="exceeds"):
        ingest_transcript_file(oversized)


def test_otlp_import_decodes_values_orders_events_and_redacts() -> None:
    bundle = import_otlp_data(
        otlp_data(otlp_span()),
        provenance(EvidenceFormat.OTLP_JSON),
        capsule_id="capsule-otlp",
    )
    trace = bundle.traces[0]
    assert trace.trace_id == TRACE_ID
    assert [event.event_type for event in trace.events] == [
        TraceEventType.SPAN_STARTED,
        TraceEventType.SPAN_EVENT_OBSERVED,
        TraceEventType.SPAN_COMPLETED,
    ]
    assert trace.events[0].context.capsule_id == "capsule-otlp"
    attributes = trace.events[0].attributes["span_attributes"]
    assert attributes["boolean"] is True
    assert attributes["integer"] == 7
    assert attributes["double"] == 2.5
    assert attributes["bytes"] == "YWJj"
    assert attributes["array"] == ["one", 2]
    assert attributes["mapping"] == {"nested": "value"}
    assert trace.events[-1].payload["duration_nanoseconds"] == 1_000_000_000
    serialized = bundle.model_dump_json()
    assert "alex@example.com" not in serialized
    assert "sk-abcdefghijklmnop" not in serialized


def test_otlp_import_groups_and_sorts_trace_ids() -> None:
    second_trace = "ffffffffffffffffffffffffffffffff"
    bundle = import_otlp_data(
        otlp_data(
            otlp_span(trace_id=second_trace, span_id="ffffffffffffffff"),
            otlp_span(),
        ),
        provenance(EvidenceFormat.OTLP_JSON, redact=False),
    )
    assert [trace.trace_id for trace in bundle.traces] == [TRACE_ID, second_trace]


def test_otlp_overlapping_spans_have_stable_event_order() -> None:
    child = otlp_span(
        span_id="1111111111111111",
        parent_span_id=SPAN_ID,
        start_ns=START_NS + 100,
        end_ns=START_NS + 200,
    )
    parent = otlp_span(start_ns=START_NS, end_ns=START_NS + 300)
    for span in (parent, child):
        span["events"] = []
    bundle = import_otlp_data(
        otlp_data(child, parent),
        provenance(EvidenceFormat.OTLP_JSON, redact=False),
    )
    assert [event.payload["span_id"] for event in bundle.traces[0].events] == [
        SPAN_ID,
        "1111111111111111",
        "1111111111111111",
        SPAN_ID,
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda span: span.update(traceId="bad"), "traceId"),
        (lambda span: span.update(spanId="bad"), "spanId"),
        (lambda span: span.update(parentSpanId="bad"), "parentSpanId"),
        (
            lambda span: span.update(
                startTimeUnixNano=str(START_NS + 2), endTimeUnixNano=str(START_NS + 1)
            ),
            "ends before",
        ),
        (lambda span: span.update(name=""), "name"),
        (lambda span: span.update(startTimeUnixNano="-1"), "non-negative"),
    ],
)
def test_otlp_rejects_invalid_span_fields(change: Any, message: str) -> None:
    span = otlp_span(parent_span_id="1111111111111111")
    change(span)
    with pytest.raises(EvidenceIngestError, match=message):
        import_otlp_data(
            otlp_data(span), provenance(EvidenceFormat.OTLP_JSON, redact=False)
        )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({}, "exactly one"),
        ({"stringValue": "x", "intValue": "1"}, "exactly one"),
        ({"boolValue": "true"}, "boolean"),
        ({"intValue": True}, "integer"),
        ({"doubleValue": True}, "number"),
        ({"arrayValue": {"values": {}}}, "array"),
        ({"kvlistValue": {"values": {}}}, "array"),
    ],
)
def test_otlp_rejects_invalid_any_values(value: Any, message: str) -> None:
    span = otlp_span()
    span["attributes"] = [{"key": "bad", "value": value}]
    with pytest.raises(EvidenceIngestError, match=message):
        import_otlp_data(
            otlp_data(span), provenance(EvidenceFormat.OTLP_JSON, redact=False)
        )


def test_otlp_rejects_duplicate_attributes_and_bad_events() -> None:
    span = otlp_span()
    span["attributes"] = [
        {"key": "same", "value": {"stringValue": "one"}},
        {"key": "same", "value": {"stringValue": "two"}},
    ]
    with pytest.raises(EvidenceIngestError, match="duplicate"):
        import_otlp_data(
            otlp_data(span), provenance(EvidenceFormat.OTLP_JSON, redact=False)
        )

    span = otlp_span()
    span["events"] = [{"timeUnixNano": str(START_NS), "name": ""}]
    with pytest.raises(EvidenceIngestError, match="name"):
        import_otlp_data(
            otlp_data(span), provenance(EvidenceFormat.OTLP_JSON, redact=False)
        )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({}, "resourceSpans"),
        ({"resourceSpans": []}, "no spans"),
        ({"resourceSpans": [{}]}, "no spans"),
        ({"resourceSpans": "bad"}, "array"),
    ],
)
def test_otlp_rejects_invalid_export_roots(data: dict[str, Any], message: str) -> None:
    with pytest.raises(EvidenceIngestError, match=message):
        import_otlp_data(data, provenance(EvidenceFormat.OTLP_JSON, redact=False))


def test_ingest_otlp_file_records_provenance_and_wraps_json(tmp_path: Path) -> None:
    path = tmp_path / "otel.json"
    raw = json.dumps(otlp_data(otlp_span())).encode()
    path.write_bytes(raw)
    bundle = ingest_otlp_file(path, capsule_id="capsule", redact=False)
    assert bundle.provenance.sha256 == hashlib.sha256(raw).hexdigest()
    assert bundle.provenance.format is EvidenceFormat.OTLP_JSON
    assert bundle.traces[0].events[0].context.capsule_id == "capsule"

    path.write_text("{}", encoding="utf-8")
    with pytest.raises(EvidenceIngestError, match="resourceSpans"):
        ingest_otlp_file(path)


def test_otlp_timestamp_outside_supported_range() -> None:
    span = otlp_span()
    span["startTimeUnixNano"] = str(10**40)
    with pytest.raises(EvidenceIngestError, match="timestamp range"):
        import_otlp_data(
            otlp_data(span), provenance(EvidenceFormat.OTLP_JSON, redact=False)
        )


def test_otlp_rejects_too_many_spans() -> None:
    span = otlp_span()
    data = otlp_data(*([span] * 10_001))
    with pytest.raises(EvidenceIngestError, match="10000 spans"):
        import_otlp_data(data, provenance(EvidenceFormat.OTLP_JSON, redact=False))


def test_redaction_record_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    import e2h.ingest as ingest

    monkeypatch.setattr(ingest, "_MAX_RECORDS", 1)
    data = transcript_data()
    data["messages"] = [
        {
            "id": "m1",
            "role": "user",
            "content": "alex@example.com bob@example.com",
            "timestamp": "2026-08-05T12:00:00Z",
        }
    ]
    document = TranscriptDocument.model_validate(data)
    with pytest.raises(EvidenceIngestError, match="redactions"):
        import_transcript_document(
            document, provenance(EvidenceFormat.TRANSCRIPT_JSON)
        )


def test_source_provenance_validation() -> None:
    with pytest.raises(ValidationError):
        SourceProvenance(
            format=EvidenceFormat.TRANSCRIPT_JSON,
            source_name="source.json",
            sha256="bad",
            size_bytes=1,
            redaction_enabled=True,
        )


def test_transcript_message_timestamp_accepts_aware_values() -> None:
    message = TranscriptMessage(
        id="m1",
        role="system",
        content="hello",
        timestamp=datetime(2026, 8, 5, tzinfo=UTC) + timedelta(seconds=1),
    )
    assert message.timestamp.tzinfo is UTC
