from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.anthropic_messages import AnthropicMessageRecord, AnthropicMessagesDocument
from e2h.anthropic_runtime import AnthropicMessagesRequest, AnthropicMessagesRuntimeResult
from e2h.gemini_generate_content import GeminiGenerateContentDocument, GeminiGenerateContentRecord
from e2h.gemini_runtime import GeminiGenerateContentRequest, GeminiGenerateContentRuntimeResult
from e2h.openai_responses import OpenAIResponseRecord, OpenAIResponsesDocument
from e2h.openai_runtime import OpenAIResponsesRequest, OpenAIResponsesRuntimeResult

SHA = "a" * 64
NOW = datetime(2026, 8, 10, 21, 15, tzinfo=UTC)


def _sha256(value: Any) -> str:
    rendered = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def test_openai_runtime_result_revalidates_mutated_request_identity() -> None:
    body: dict[str, Any] = {}
    request = OpenAIResponsesRequest(
        invocation_id="invocation",
        variant_id="variant",
        variant_sha256=SHA,
        variant_document_sha256=SHA,
        base_capsule_sha256=SHA,
        route_target_id="route",
        model="model",
        body=body,
        request_sha256=_sha256(body),
    )
    request.invocation_id = "invalid invocation id"
    archive = OpenAIResponsesDocument(
        id="archive",
        responses=[
            OpenAIResponseRecord(
                response={
                    "id": "response",
                    "object": "response",
                    "created_at": 1,
                    "model": "model",
                    "output": [],
                }
            )
        ],
    )

    with pytest.raises(ValidationError) as exc_info:
        OpenAIResponsesRuntimeResult(request=request, archive=archive)

    assert exc_info.value.errors()[0]["loc"][-1] == "invocation_id"


def test_anthropic_runtime_result_revalidates_mutated_request_identity() -> None:
    body: dict[str, Any] = {}
    request = AnthropicMessagesRequest(
        invocation_id="invocation",
        variant_id="variant",
        variant_sha256=SHA,
        variant_document_sha256=SHA,
        base_capsule_sha256=SHA,
        route_target_id="route",
        model="model",
        body=body,
        request_sha256=_sha256(body),
    )
    request.invocation_id = "invalid invocation id"
    archive = AnthropicMessagesDocument(
        id="archive",
        records=[
            AnthropicMessageRecord(
                timestamp=NOW,
                response={
                    "id": "response",
                    "type": "message",
                    "role": "assistant",
                    "model": "model",
                    "content": [],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        ],
    )

    with pytest.raises(ValidationError) as exc_info:
        AnthropicMessagesRuntimeResult(request=request, archive=archive)

    assert exc_info.value.errors()[0]["loc"][-1] == "invocation_id"


def test_gemini_runtime_result_revalidates_mutated_request_identity() -> None:
    body: dict[str, Any] = {}
    request = GeminiGenerateContentRequest(
        invocation_id="invocation",
        variant_id="variant",
        variant_sha256=SHA,
        variant_document_sha256=SHA,
        base_capsule_sha256=SHA,
        route_target_id="route",
        model="model",
        endpoint="https://example.invalid/model",
        body=body,
        request_sha256=_sha256({"model": "model", "body": body}),
    )
    request.invocation_id = "invalid invocation id"
    archive = GeminiGenerateContentDocument(
        id="archive",
        records=[
            GeminiGenerateContentRecord(
                timestamp=NOW,
                response={"responseId": "response", "candidates": []},
                candidate_ids=[],
            )
        ],
    )

    with pytest.raises(ValidationError) as exc_info:
        GeminiGenerateContentRuntimeResult(request=request, archive=archive)

    assert exc_info.value.errors()[0]["loc"][-1] == "invocation_id"
