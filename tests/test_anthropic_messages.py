from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.anthropic_messages import (
    AnthropicMessagesDocument,
    import_anthropic_messages_document,
    ingest_anthropic_messages_file,
)
from e2h.ingest import EvidenceFormat, EvidenceIngestError, SourceProvenance
from e2h.privacy import CustomRedactionRule, RedactionPolicy
from e2h.trace import TraceEventType


def provenance(*, redact: bool = True) -> SourceProvenance:
    policy = RedactionPolicy()
    from e2h.privacy import redaction_policy_sha256

    return SourceProvenance(
        format=EvidenceFormat.ANTHROPIC_MESSAGES_JSON,
        source_name="anthropic.json",
        sha256="a" * 64,
        size_bytes=100,
        redaction_enabled=redact,
        redaction_policy_id=policy.id,
        redaction_policy_sha256=redaction_policy_sha256(policy),
    )


def response(response_id: str, content: list[dict], **overrides) -> dict:
    payload = {
        "id": response_id,
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 20,
            "output_tokens": 8,
            "cache_read_input_tokens": 4,
            "service_tier": "standard",
        },
    }
    payload.update(overrides)
    return payload


def archive_document() -> dict:
    first = datetime(2026, 8, 6, 8, 0, tzinfo=UTC)
    tool_call = {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "lookup_customer",
        "input": {"email": "alice@example.com"},
    }
    hidden = {
        "type": "thinking",
        "thinking": "private chain of thought",
        "signature": "opaque-signature-value",
    }
    first_response_content = [hidden, tool_call]
    return {
        "schema_version": "0.1",
        "id": "anthropic-conversation",
        "capsule_id": "provider-capsule",
        "metadata": {"environment": "test"},
        "records": [
            {
                "timestamp": first.isoformat(),
                "request_id": "req_1",
                "system": [{"type": "text", "text": "Keep API key sk-abcdefghijklmnop safe"}],
                "messages": [
                    {
                        "id": "msg_user_1",
                        "role": "user",
                        "content": [{"type": "text", "text": "Find alice@example.com"}],
                    }
                ],
                "response": response(
                    "msg_assistant_1",
                    first_response_content,
                    stop_reason="tool_use",
                ),
            },
            {
                "timestamp": (first + timedelta(seconds=1)).isoformat(),
                "request_id": "req_2",
                "messages": [
                    {
                        "id": "msg_user_1",
                        "role": "user",
                        "content": [{"type": "text", "text": "Find alice@example.com"}],
                    },
                    {
                        "id": "msg_assistant_1",
                        "role": "assistant",
                        "content": first_response_content,
                    },
                    {
                        "id": "msg_user_2",
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": "Customer phone +61 412 345 678",
                            }
                        ],
                    },
                ],
                "response": response(
                    "msg_assistant_2",
                    [
                        {
                            "type": "text",
                            "text": "Customer located.",
                            "citations": [
                                {
                                    "type": "web_search_result_location",
                                    "url": "https://example.com/customer",
                                    "title": "Customer record",
                                }
                            ],
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "raw-image-data-must-not-be-retained",
                            },
                        },
                    ],
                    container={"id": "container_1", "expires_at": "2026-08-06T09:00:00Z"},
                ),
            },
        ],
    }


def bundle_from(data: dict, *, redact: bool = True, policy: RedactionPolicy | None = None):
    document = AnthropicMessagesDocument.model_validate(data)
    return import_anthropic_messages_document(
        document,
        provenance(redact=redact),
        redaction_policy=policy,
    )


def test_chained_archive_normalizes_messages_tools_metadata_and_deduplicates() -> None:
    bundle = bundle_from(archive_document())
    trace = bundle.traces[0]
    event_types = [event.event_type for event in trace.events]
    assert event_types.count(TraceEventType.MESSAGE_OBSERVED) == 5
    assert event_types.count(TraceEventType.TOOL_CALLED) == 1
    assert event_types.count(TraceEventType.TOOL_COMPLETED) == 1
    messages = [
        event.attributes["message_id"]
        for event in trace.events
        if event.event_type is TraceEventType.MESSAGE_OBSERVED
    ]
    assert messages.count("msg_user_1") == 1
    assert messages.count("msg_assistant_1") == 1
    completed = next(
        event for event in trace.events if event.event_type is TraceEventType.TOOL_COMPLETED
    )
    assert completed.attributes["linked_call"] is True
    assert completed.attributes["tool_name"] == "lookup_customer"
    assert completed.attributes["server_tool"] is False
    responses = [
        event
        for event in trace.events
        if event.attributes.get("artifact_kind") == "anthropic.message"
    ]
    assert len(responses) == 2
    assert responses[0].payload["usage"]["input_tokens"] == 20
    assert responses[0].payload["stop_reason"] == "tool_use"
    assert responses[1].payload["container"]["id"] == "container_1"
    assert trace.events[-1].payload["record_count"] == 2
    assert trace.events[-1].payload["tool_use_count"] == 1


def test_hidden_thinking_signature_and_image_bytes_are_never_retained() -> None:
    bundle = bundle_from(archive_document(), redact=False)
    rendered = bundle.model_dump_json()
    assert "private chain of thought" not in rendered
    assert "opaque-signature-value" not in rendered
    assert "raw-image-data-must-not-be-retained" not in rendered
    thinking = next(
        event
        for event in bundle.traces[0].events
        if event.attributes.get("artifact_kind") == "anthropic.thinking_presence"
    )
    assert thinking.payload == {
        "type": "thinking",
        "thinking_present": True,
        "signature_present": True,
        "redacted_data_present": False,
    }
    assistant = next(
        event
        for event in bundle.traces[0].events
        if event.attributes.get("message_id") == "msg_assistant_1"
        and event.event_type is TraceEventType.MESSAGE_OBSERVED
    )
    assert "[thinking]" in assistant.payload["content"]


def test_default_redaction_covers_system_messages_arguments_and_results() -> None:
    bundle = bundle_from(archive_document())
    rendered = bundle.model_dump_json()
    assert "alice@example.com" not in rendered
    assert "sk-abcdefghijklmnop" not in rendered
    assert "+61 412 345 678" not in rendered
    assert bundle.redaction_review is not None
    assert bundle.redaction_review.residual_findings == []
    assert bundle.redaction_review.counts_by_kind["email"] >= 2
    assert bundle.redaction_review.counts_by_kind["secret"] >= 1
    assert bundle.redaction_review.counts_by_kind["phone"] >= 1


def test_custom_policy_and_allowlist_apply_to_provider_evidence() -> None:
    data = archive_document()
    data["records"][1]["response"]["content"][0]["text"] = (
        "Case CASE-123456 for support@example.com and owner@example.com"
    )
    policy = RedactionPolicy(
        id="anthropic-policy",
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
    assert bundle.redaction_review.policy_id == "anthropic-policy"
    assert bundle.redaction_review.counts_by_rule == {"case-id": 1}
    assert "support@example.com" not in report


def test_review_only_mode_retains_evidence_and_reports_hashed_residuals() -> None:
    bundle = bundle_from(archive_document(), redact=False)
    rendered = bundle.model_dump_json()
    report = bundle.redaction_review.model_dump_json() if bundle.redaction_review else ""
    assert "alice@example.com" in rendered
    assert bundle.redactions == []
    assert bundle.redaction_review is not None
    assert bundle.redaction_review.redaction_enabled is False
    assert bundle.redaction_review.residual_findings
    assert "alice@example.com" not in report
    assert "redaction_disabled_review_only" in bundle.redaction_review.warnings


def test_server_tool_use_and_result_are_linked() -> None:
    data = archive_document()
    data["records"][0]["response"]["content"] = [
        {
            "type": "server_tool_use",
            "id": "srv_1",
            "name": "web_search",
            "input": {"query": "E2H"},
            "caller": {"type": "direct"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srv_1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://example.com/e2h",
                    "title": "E2H",
                    "encrypted_content": "opaque-search-token",
                }
            ],
        },
    ]
    data["records"] = data["records"][:1]
    bundle = bundle_from(data, redact=False)
    called = next(
        event for event in bundle.traces[0].events if event.event_type is TraceEventType.TOOL_CALLED
    )
    completed = next(
        event
        for event in bundle.traces[0].events
        if event.event_type is TraceEventType.TOOL_COMPLETED
    )
    assert called.attributes["server_tool"] is True
    assert completed.attributes["server_tool"] is True
    assert completed.attributes["linked_call"] is True
    assert completed.attributes["tool_name"] == "web_search"


def test_unlinked_tool_result_is_preserved_explicitly() -> None:
    data = archive_document()
    data["records"] = [
        {
            "timestamp": "2026-08-06T08:00:00Z",
            "messages": [
                {
                    "id": "result-only",
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "missing",
                            "content": "partial result",
                            "is_error": True,
                        }
                    ],
                }
            ],
            "response": response("response-only", [{"type": "text", "text": "Observed"}]),
        }
    ]
    bundle = bundle_from(data, redact=False)
    completed = next(
        event
        for event in bundle.traces[0].events
        if event.event_type is TraceEventType.TOOL_COMPLETED
    )
    assert completed.attributes["linked_call"] is False
    assert completed.attributes["tool_name"] is None
    assert completed.attributes["is_error"] is True


def test_unknown_blocks_are_preserved_as_observable_artifacts() -> None:
    data = archive_document()
    data["records"] = data["records"][:1]
    data["records"][0]["response"]["content"] = [
        {"type": "future_provider_block", "status": "completed", "value": "observable"}
    ]
    bundle = bundle_from(data, redact=False)
    artifact = next(
        event
        for event in bundle.traces[0].events
        if event.attributes.get("block_type") == "future_provider_block"
    )
    assert artifact.payload["metadata"]["value"] == "observable"


def test_text_citations_and_multimodal_placeholders_are_observable() -> None:
    bundle = bundle_from(archive_document(), redact=False)
    message = next(
        event
        for event in bundle.traces[0].events
        if event.attributes.get("message_id") == "msg_assistant_2"
        and event.event_type is TraceEventType.MESSAGE_OBSERVED
    )
    assert message.payload["content"] == "Customer located.\n[image]"
    text_block = message.payload["blocks"][0]
    assert text_block["citations"][0]["title"] == "Customer record"
    image_block = message.payload["blocks"][1]
    assert image_block["source"] == {"type": "base64", "media_type": "image/png"}


def test_capsule_override_and_provenance_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "anthropic.json"
    path.write_text(json.dumps(archive_document()), encoding="utf-8")
    bundle = ingest_anthropic_messages_file(path, capsule_id="override")
    assert bundle.provenance.format is EvidenceFormat.ANTHROPIC_MESSAGES_JSON
    assert bundle.provenance.source_name == "anthropic.json"
    assert bundle.provenance.redaction_policy_id == "default"
    assert bundle.traces[0].events[0].context.capsule_id == "override"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda data: data["records"].append(copy.deepcopy(data["records"][0])),
            "response ids must be unique",
        ),
        (
            lambda data: data["records"][1].update(
                {"timestamp": "2026-08-06T07:59:00Z"}
            ),
            "record timestamps must be nondecreasing",
        ),
        (
            lambda data: data["records"][1]["messages"][0].update(
                {"content": [{"type": "text", "text": "changed"}]}
            ),
            "message ids must retain identical observable content",
        ),
        (
            lambda data: data["records"][1]["messages"][1]["content"][1].update(
                {"name": "different_tool"}
            ),
            "tool use ids must retain identical definitions",
        ),
        (
            lambda data: data["records"][0]["response"].update({"type": "completion"}),
            "response.type must be 'message'",
        ),
        (
            lambda data: data["records"][0]["response"].update({"usage": {}}),
            "response.usage.input_tokens must be a non-negative integer",
        ),
    ],
)
def test_invalid_archive_envelopes_are_rejected(mutation, message: str) -> None:
    data = archive_document()
    mutation(data)
    with pytest.raises(ValidationError, match=message):
        AnthropicMessagesDocument.model_validate(data)


def test_naive_record_timestamp_is_rejected() -> None:
    data = archive_document()
    data["records"][0]["timestamp"] = "2026-08-06T08:00:00"
    with pytest.raises(ValidationError, match="timestamp must be timezone-aware"):
        AnthropicMessagesDocument.model_validate(data)


def test_malformed_content_is_reported_as_ingest_error() -> None:
    data = archive_document()
    data["records"][0]["messages"][0]["content"] = "plain string"
    document = AnthropicMessagesDocument.model_validate(data)
    bundle = import_anthropic_messages_document(document, provenance())
    assert "plain string" in bundle.traces[0].events[2].payload["content"]


def test_file_loader_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "anthropic.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(EvidenceIngestError, match="invalid evidence JSON"):
        ingest_anthropic_messages_file(path)


def test_file_loader_rejects_invalid_document(tmp_path: Path) -> None:
    path = tmp_path / "anthropic.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(EvidenceIngestError, match="records"):
        ingest_anthropic_messages_file(path)
