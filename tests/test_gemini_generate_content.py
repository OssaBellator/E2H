from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.gemini_generate_content import (
    GeminiGenerateContentDocument,
    import_gemini_generate_content_document,
    ingest_gemini_generate_content_file,
)
from e2h.ingest import EvidenceFormat, EvidenceIngestError, SourceProvenance
from e2h.privacy import CustomRedactionRule, RedactionPolicy, redaction_policy_sha256
from e2h.trace import TraceEventType


def provenance(*, redact: bool = True) -> SourceProvenance:
    policy = RedactionPolicy()
    return SourceProvenance(
        format=EvidenceFormat.GEMINI_GENERATE_CONTENT_JSON,
        source_name="gemini.json",
        sha256="b" * 64,
        size_bytes=100,
        redaction_enabled=redact,
        redaction_policy_id=policy.id,
        redaction_policy_sha256=redaction_policy_sha256(policy),
    )


def response(response_id: str, parts: list[dict], **overrides) -> dict:
    payload = {
        "responseId": response_id,
        "modelVersion": "gemini-2.5-pro-001",
        "candidates": [
            {
                "index": 0,
                "content": {"role": "model", "parts": parts},
                "finishReason": "STOP",
                "tokenCount": 8,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 8,
            "thoughtsTokenCount": 3,
            "totalTokenCount": 31,
        },
    }
    payload.update(overrides)
    return payload


def archive_document() -> dict:
    first = datetime(2026, 8, 6, 8, 0, tzinfo=UTC)
    model_parts = [
        {
            "thought": True,
            "text": "private Gemini reasoning",
            "thoughtSignature": "opaque-thought-signature",
        },
        {
            "functionCall": {
                "id": "call_1",
                "name": "lookup_customer",
                "args": {"email": "alice@example.com"},
            }
        },
    ]
    return {
        "schema_version": "0.1",
        "id": "gemini-conversation",
        "capsule_id": "provider-capsule",
        "metadata": {"environment": "test"},
        "records": [
            {
                "timestamp": first.isoformat(),
                "request_id": "req_1",
                "model": "gemini-2.5-pro",
                "system_instruction": {
                    "id": "system_1",
                    "role": "system",
                    "parts": [{"text": "Keep API key sk-abcdefghijklmnop safe"}],
                },
                "contents": [
                    {
                        "id": "user_1",
                        "role": "user",
                        "parts": [{"text": "Find alice@example.com"}],
                    }
                ],
                "candidate_ids": ["model_1"],
                "response": response("resp_1", model_parts),
            },
            {
                "timestamp": (first + timedelta(seconds=1)).isoformat(),
                "request_id": "req_2",
                "model": "gemini-2.5-pro",
                "contents": [
                    {
                        "id": "user_1",
                        "role": "user",
                        "parts": [{"text": "Find alice@example.com"}],
                    },
                    {
                        "id": "model_1",
                        "role": "model",
                        "parts": copy.deepcopy(model_parts),
                    },
                    {
                        "id": "user_2",
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "id": "call_1",
                                    "name": "lookup_customer",
                                    "response": {"phone": "+61 412 345 678"},
                                }
                            }
                        ],
                    },
                ],
                "candidate_ids": ["model_2"],
                "response": response(
                    "resp_2",
                    [
                        {"text": "Customer located."},
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": "raw-image-bytes",
                            }
                        },
                    ],
                    promptFeedback={"blockReason": None},
                ),
            },
        ],
    }


def bundle_from(data: dict, *, redact: bool = True, policy: RedactionPolicy | None = None):
    document = GeminiGenerateContentDocument.model_validate(data)
    return import_gemini_generate_content_document(
        document,
        provenance(redact=redact),
        redaction_policy=policy,
    )


def test_chained_archive_deduplicates_messages_and_links_function_lifecycle() -> None:
    bundle = bundle_from(archive_document())
    trace = bundle.traces[0]
    event_types = [event.event_type for event in trace.events]
    assert event_types.count(TraceEventType.MESSAGE_OBSERVED) == 5
    assert event_types.count(TraceEventType.TOOL_CALLED) == 1
    assert event_types.count(TraceEventType.TOOL_COMPLETED) == 1
    message_ids = [
        event.attributes["message_id"]
        for event in trace.events
        if event.event_type is TraceEventType.MESSAGE_OBSERVED
    ]
    assert message_ids.count("user_1") == 1
    assert message_ids.count("model_1") == 1
    completed = next(
        event for event in trace.events if event.event_type is TraceEventType.TOOL_COMPLETED
    )
    assert completed.attributes["linked_call"] is True
    assert completed.attributes["tool_name"] == "lookup_customer"
    candidates = [
        event
        for event in trace.events
        if event.attributes.get("artifact_kind") == "gemini.candidate"
    ]
    assert len(candidates) == 2
    responses = [
        event
        for event in trace.events
        if event.attributes.get("artifact_kind") == "gemini.response"
    ]
    assert responses[0].payload["usage_metadata"]["totalTokenCount"] == 31
    assert responses[0].payload["model_version"] == "gemini-2.5-pro-001"
    assert trace.events[-1].payload == {
        "record_count": 2,
        "observable_content_count": 5,
        "call_count": 1,
    }


def test_thought_text_signature_and_inline_bytes_are_never_retained() -> None:
    bundle = bundle_from(archive_document(), redact=False)
    rendered = bundle.model_dump_json()
    assert "private Gemini reasoning" not in rendered
    assert "opaque-thought-signature" not in rendered
    assert "raw-image-bytes" not in rendered
    thought = next(
        event
        for event in bundle.traces[0].events
        if event.attributes.get("artifact_kind") == "gemini.thought_presence"
    )
    assert thought.payload == {
        "type": "thought",
        "text_present": True,
        "thought_signature_present": True,
    }
    message = next(
        event
        for event in bundle.traces[0].events
        if event.attributes.get("message_id") == "model_1"
        and event.event_type is TraceEventType.MESSAGE_OBSERVED
    )
    assert "[thought]" in message.payload["content"]


def test_default_redaction_covers_system_messages_args_and_results() -> None:
    bundle = bundle_from(archive_document())
    rendered = bundle.model_dump_json()
    assert "alice@example.com" not in rendered
    assert "sk-abcdefghijklmnop" not in rendered
    assert "+61 412 345 678" not in rendered
    assert bundle.redaction_review is not None
    assert bundle.redaction_review.residual_findings == []
    assert bundle.redaction_review.counts_by_kind == {
        "email": 2,
        "phone": 1,
        "secret": 1,
    }


def test_custom_policy_and_allowlist_apply_to_gemini_evidence() -> None:
    data = archive_document()
    data["records"][1]["response"]["candidates"][0]["content"]["parts"][0]["text"] = (
        "Case CASE-123456 for support@example.com and owner@example.com"
    )
    policy = RedactionPolicy(
        id="gemini-policy",
        custom_rules=[CustomRedactionRule(id="case-id", pattern=r"CASE-\d{6}")],
        allow_values=["support@example.com"],
    )
    bundle = bundle_from(data, policy=policy)
    rendered = bundle.model_dump_json()
    report = bundle.redaction_review.model_dump_json() if bundle.redaction_review else ""
    assert "CASE-123456" not in rendered
    assert "owner@example.com" not in rendered
    assert "support@example.com" in rendered
    assert bundle.redaction_review is not None
    assert bundle.redaction_review.policy_id == "gemini-policy"
    assert bundle.redaction_review.counts_by_rule == {"case-id": 1}
    assert "support@example.com" not in report


def test_review_only_mode_retains_visible_evidence_and_hashes_residuals() -> None:
    bundle = bundle_from(archive_document(), redact=False)
    rendered = bundle.model_dump_json()
    report = bundle.redaction_review.model_dump_json() if bundle.redaction_review else ""
    assert "alice@example.com" in rendered
    assert bundle.redactions == []
    assert bundle.redaction_review is not None
    assert bundle.redaction_review.redaction_enabled is False
    assert bundle.redaction_review.residual_findings
    assert "alice@example.com" not in report


def test_server_tool_and_code_execution_lifecycles_are_linked() -> None:
    data = archive_document()
    data["records"] = data["records"][:1]
    data["records"][0]["candidate_ids"] = ["model_tools"]
    data["records"][0]["response"] = response(
        "resp_tools",
        [
            {
                "toolCall": {
                    "id": "tool_1",
                    "toolType": "URL_CONTEXT",
                    "args": {"url": "https://example.com"},
                }
            },
            {
                "toolResponse": {
                    "id": "tool_1",
                    "toolType": "URL_CONTEXT",
                    "response": {"status": "ok"},
                }
            },
            {"executableCode": {"id": "code_1", "language": "PYTHON", "code": "print('ok')"}},
            {"codeExecutionResult": {"id": "code_1", "outcome": "OUTCOME_OK", "output": "ok"}},
        ],
    )
    bundle = bundle_from(data, redact=False)
    calls = [
        event for event in bundle.traces[0].events if event.event_type is TraceEventType.TOOL_CALLED
    ]
    results = [
        event
        for event in bundle.traces[0].events
        if event.event_type is TraceEventType.TOOL_COMPLETED
    ]
    assert len(calls) == 2
    assert len(results) == 2
    assert all(event.attributes["linked_call"] is True for event in results)
    assert {event.attributes["result_kind"] for event in results} == {
        "tool_response",
        "code_execution_result",
    }


def test_file_and_inline_parts_preserve_safe_metadata_only() -> None:
    bundle = bundle_from(archive_document(), redact=False)
    model_2 = next(
        event
        for event in bundle.traces[0].events
        if event.attributes.get("message_id") == "model_2"
        and event.event_type is TraceEventType.MESSAGE_OBSERVED
    )
    assert model_2.payload["content"] == "Customer located.\n[inline_data:image/png]"
    assert model_2.payload["parts"][1] == {"type": "inline_data", "mime_type": "image/png"}


def test_unlinked_function_response_is_preserved() -> None:
    data = archive_document()
    data["records"] = [
        {
            "timestamp": "2026-08-06T08:00:00Z",
            "contents": [
                {
                    "id": "result_only",
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "id": "missing",
                                "name": "partial",
                                "response": {"status": "unknown"},
                            }
                        }
                    ],
                }
            ],
            "candidate_ids": ["model_only"],
            "response": response("response_only", [{"text": "Observed"}]),
        }
    ]
    bundle = bundle_from(data, redact=False)
    completed = next(
        event
        for event in bundle.traces[0].events
        if event.event_type is TraceEventType.TOOL_COMPLETED
    )
    assert completed.attributes["linked_call"] is False
    assert completed.attributes["tool_name"] == "partial"


def test_capsule_override_and_provenance_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "gemini.json"
    path.write_text(json.dumps(archive_document()), encoding="utf-8")
    bundle = ingest_gemini_generate_content_file(path, capsule_id="override")
    assert bundle.provenance.format is EvidenceFormat.GEMINI_GENERATE_CONTENT_JSON
    assert bundle.provenance.source_name == "gemini.json"
    assert bundle.traces[0].events[0].context.capsule_id == "override"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda data: data["records"].append(copy.deepcopy(data["records"][0])),
            "response ids must be unique",
        ),
        (
            lambda data: data["records"][1].update({"timestamp": "2026-08-06T07:59:00Z"}),
            "record timestamps must be nondecreasing",
        ),
        (
            lambda data: data["records"][1]["contents"][0].update({"parts": [{"text": "changed"}]}),
            "content ids must retain identical observable content",
        ),
        (
            lambda data: data["records"][1]["contents"][1]["parts"][1]["functionCall"].update(
                {"name": "different_tool"}
            ),
            "function call ids must retain identical definitions",
        ),
        (
            lambda data: data["records"][0]["response"].pop("responseId"),
            "response.response_id must be a non-empty string",
        ),
        (
            lambda data: data["records"][0]["response"].update({"candidates": {}}),
            "response.candidates must be an array",
        ),
        (
            lambda data: data["records"][0].update({"candidate_ids": []}),
            "candidate_ids must align with response candidates",
        ),
    ],
)
def test_invalid_archive_envelopes_are_rejected(mutation, message: str) -> None:
    data = archive_document()
    mutation(data)
    with pytest.raises(ValidationError, match=message):
        GeminiGenerateContentDocument.model_validate(data)


def test_naive_timestamp_is_rejected() -> None:
    data = archive_document()
    data["records"][0]["timestamp"] = "2026-08-06T08:00:00"
    with pytest.raises(ValidationError, match="timestamp must be timezone-aware"):
        GeminiGenerateContentDocument.model_validate(data)


def test_file_loader_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "gemini.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(EvidenceIngestError, match="invalid evidence JSON"):
        ingest_gemini_generate_content_file(path)


def test_file_loader_rejects_invalid_document(tmp_path: Path) -> None:
    path = tmp_path / "gemini.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(EvidenceIngestError, match="records"):
        ingest_gemini_generate_content_file(path)
