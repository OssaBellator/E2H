from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.ingest import EvidenceFormat, EvidenceIngestError, SourceProvenance
from e2h.openai_responses import (
    OpenAIResponsesDocument,
    import_openai_responses_document,
    ingest_openai_responses_file,
)
from e2h.trace import TraceEventType


def response(
    response_id: str,
    created_at: float,
    output: list[dict[str, object]],
    **extra: object,
) -> dict[str, object]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "model": "gpt-5.5",
        "status": "completed",
        "output": output,
        "parallel_tool_calls": True,
        **extra,
    }


def message(
    message_id: str,
    role: str,
    text: str,
    *,
    output: bool = False,
) -> dict[str, object]:
    return {
        "id": message_id,
        "type": "message",
        "role": role,
        "status": "completed",
        "content": [
            {
                "type": "output_text" if output else "input_text",
                "text": text,
                **({"annotations": []} if output else {}),
            }
        ],
    }


def export_document() -> dict[str, object]:
    user = message("msg_user", "user", "Look up account alice@example.com")
    call = {
        "id": "fc_item",
        "type": "function_call",
        "call_id": "call_1",
        "name": "lookup_account",
        "arguments": '{"email":"alice@example.com","token":"sk-secret123456"}',
        "status": "completed",
    }
    return {
        "schema_version": "0.1",
        "id": "openai_conversation_1",
        "capsule_id": "support-capsule",
        "metadata": {"environment": "test"},
        "responses": [
            {
                "request_id": "req_1",
                "input_items": [user],
                "response": response(
                    "resp_1",
                    1_786_000_000,
                    [
                        call,
                        {
                            "id": "reason_1",
                            "type": "reasoning",
                            "summary": [{"type": "summary_text", "text": "Use the account tool."}],
                            "content": [
                                {"type": "reasoning_text", "text": "private chain of thought"}
                            ],
                            "encrypted_content": "encrypted-private-reasoning",
                            "status": "completed",
                        },
                    ],
                    instructions="Follow the support policy.",
                    usage={
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "input_tokens_details": {
                            "cached_tokens": 0,
                            "cache_write_tokens": 0,
                        },
                        "output_tokens_details": {"reasoning_tokens": 2},
                    },
                ),
            },
            {
                "request_id": "req_2",
                "input_items": [
                    user,
                    call,
                    {
                        "id": "fco_item",
                        "type": "function_call_output",
                        "call_id": "call_1",
                        "name": "lookup_account",
                        "output": '{"plan":"pro","email":"alice@example.com"}',
                        "status": "completed",
                    },
                ],
                "response": response(
                    "resp_2",
                    1_786_000_001,
                    [message("msg_assistant", "assistant", "The plan is pro.", output=True)],
                    previous_response_id="resp_1",
                    conversation={"id": "conv_1"},
                ),
            },
        ],
    }


def provenance(*, redact: bool = False) -> SourceProvenance:
    return SourceProvenance(
        format=EvidenceFormat.OPENAI_RESPONSES_JSON,
        source_name="responses.json",
        sha256="a" * 64,
        size_bytes=100,
        redaction_enabled=redact,
    )


def test_chained_responses_normalize_messages_tools_and_metadata() -> None:
    document = OpenAIResponsesDocument.model_validate(export_document())
    bundle = import_openai_responses_document(document, provenance())
    trace = bundle.traces[0]

    assert trace.trace_id == "openai_conversation_1"
    assert trace.events[0].event_type is TraceEventType.CONVERSATION_STARTED
    messages = [
        event for event in trace.events if event.event_type is TraceEventType.MESSAGE_OBSERVED
    ]
    assert [event.attributes["role"] for event in messages] == [
        "developer",
        "user",
        "assistant",
    ]
    assert [event.payload["content"] for event in messages] == [
        "Follow the support policy.",
        "Look up account alice@example.com",
        "The plan is pro.",
    ]

    calls = [event for event in trace.events if event.event_type is TraceEventType.TOOL_CALLED]
    completed = [
        event for event in trace.events if event.event_type is TraceEventType.TOOL_COMPLETED
    ]
    assert len(calls) == 1
    assert calls[0].attributes["tool_call_id"] == "call_1"
    assert calls[0].payload["arguments_json"]["email"] == "alice@example.com"
    assert len(completed) == 1
    assert completed[0].attributes["linked_call"] is True
    assert completed[0].attributes["tool_name"] == "lookup_account"

    responses = [
        event
        for event in trace.events
        if event.attributes.get("artifact_kind") == "openai.response"
    ]
    assert [event.payload["response_id"] for event in responses] == ["resp_1", "resp_2"]
    assert responses[0].payload["usage"]["total_tokens"] == 15
    assert responses[0].payload["request_id"] == "req_1"
    assert responses[1].payload["previous_response_id"] == "resp_1"
    assert responses[1].payload["conversation_id"] == "conv_1"


def test_reasoning_preserves_summary_but_not_hidden_or_encrypted_content() -> None:
    bundle = import_openai_responses_document(
        OpenAIResponsesDocument.model_validate(export_document()),
        provenance(),
    )
    reasoning = next(
        event
        for event in bundle.traces[0].events
        if event.attributes.get("artifact_kind") == "openai.reasoning_summary"
    )
    assert reasoning.payload == {
        "provider_item_id": "reason_1",
        "summary": ["Use the account tool."],
        "status": "completed",
        "reasoning_content_present": True,
        "encrypted_content_present": True,
    }
    rendered = bundle.model_dump_json()
    assert "private chain of thought" not in rendered
    assert "encrypted-private-reasoning" not in rendered


def test_duplicate_items_are_emitted_once_across_response_chain() -> None:
    bundle = import_openai_responses_document(
        OpenAIResponsesDocument.model_validate(export_document()),
        provenance(),
    )
    events = bundle.traces[0].events
    assert sum(event.attributes.get("message_id") == "msg_user" for event in events) == 1
    assert sum(event.attributes.get("tool_call_id") == "call_1" for event in events) == 2


def test_redaction_covers_messages_arguments_outputs_and_metadata(tmp_path: Path) -> None:
    document = export_document()
    path = tmp_path / "responses.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    bundle = ingest_openai_responses_file(path)
    rendered = bundle.model_dump_json()
    assert "alice@example.com" not in rendered
    assert "sk-secret123456" not in rendered
    assert "<redacted:email:" in rendered
    assert "<redacted:secret:" in rendered
    assert bundle.provenance.redaction_enabled is True
    assert bundle.redactions


def test_redaction_can_be_disabled(tmp_path: Path) -> None:
    path = tmp_path / "responses.json"
    path.write_text(json.dumps(export_document()), encoding="utf-8")
    bundle = ingest_openai_responses_file(path, redact=False, capsule_id="override")
    assert "alice@example.com" in bundle.model_dump_json()
    assert bundle.traces[0].events[0].context.capsule_id == "override"
    assert bundle.redactions == []


def test_unknown_items_and_multimodal_blocks_are_preserved() -> None:
    payload = {
        "schema_version": "0.1",
        "id": "multimodal",
        "responses": [
            {
                "input_items": [
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "user",
                        "content": [
                            {"type": "input_image", "image_url": "data:image/png;base64,abc"},
                            {"type": "input_file", "file_id": "file_1"},
                        ],
                    }
                ],
                "response": response(
                    "resp_1",
                    1_786_000_000,
                    [
                        {
                            "id": "search_1",
                            "type": "file_search_call",
                            "status": "completed",
                            "queries": ["contract"],
                        }
                    ],
                ),
            }
        ],
    }
    bundle = import_openai_responses_document(
        OpenAIResponsesDocument.model_validate(payload),
        provenance(),
    )
    message_event = next(
        event
        for event in bundle.traces[0].events
        if event.event_type is TraceEventType.MESSAGE_OBSERVED
    )
    assert message_event.payload["content"] == "[input_image]\n[input_file]"
    unknown = next(
        event
        for event in bundle.traces[0].events
        if event.attributes.get("item_type") == "file_search_call"
    )
    assert unknown.payload["item"]["queries"] == ["contract"]


def test_orphan_function_output_is_explicitly_unlinked() -> None:
    payload = {
        "schema_version": "0.1",
        "id": "partial_export",
        "responses": [
            {
                "input_items": [
                    {
                        "id": "output_1",
                        "type": "function_call_output",
                        "call_id": "missing_call",
                        "output": "partial result",
                        "status": "completed",
                    }
                ],
                "response": response("resp_1", 1_786_000_000, []),
            }
        ],
    }
    bundle = import_openai_responses_document(
        OpenAIResponsesDocument.model_validate(payload),
        provenance(),
    )
    completed = next(
        event
        for event in bundle.traces[0].events
        if event.event_type is TraceEventType.TOOL_COMPLETED
    )
    assert completed.attributes["linked_call"] is False
    assert completed.attributes["tool_name"] is None


@pytest.mark.parametrize(
    ("mutation", "message_text"),
    [
        (
            lambda data: data["responses"].append(data["responses"][0]),
            "response ids must be unique",
        ),
        (
            lambda data: data["responses"][1]["response"].update({"created_at": 1}),
            "timestamps must be nondecreasing",
        ),
        (
            lambda data: data["responses"][0]["response"].update({"object": "chat.completion"}),
            "response.object must be 'response'",
        ),
        (
            lambda data: data["responses"][0]["response"].update({"output": {}}),
            "response.output must be an array",
        ),
    ],
)
def test_invalid_export_envelopes_are_rejected(mutation, message_text: str) -> None:
    data = export_document()
    mutation(data)
    with pytest.raises(ValidationError, match=message_text):
        OpenAIResponsesDocument.model_validate(data)


def test_invalid_nested_content_is_reported_as_ingest_error() -> None:
    data = export_document()
    data["responses"][0]["input_items"][0]["content"] = "not-an-array"
    document = OpenAIResponsesDocument.model_validate(data)
    with pytest.raises(EvidenceIngestError, match="content must be an array"):
        import_openai_responses_document(document, provenance())


def test_file_loader_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "responses.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(EvidenceIngestError, match="invalid evidence JSON"):
        ingest_openai_responses_file(path)


def test_conflicting_function_call_metadata_is_rejected() -> None:
    data = export_document()
    second_call = dict(data["responses"][1]["input_items"][1])
    second_call["name"] = "different_tool"
    data["responses"][1]["input_items"][1] = second_call
    document = OpenAIResponsesDocument.model_validate(data)
    with pytest.raises(EvidenceIngestError, match="conflicting provider metadata"):
        import_openai_responses_document(document, provenance())
