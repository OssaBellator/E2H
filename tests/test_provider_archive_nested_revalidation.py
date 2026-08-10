from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from e2h.anthropic_messages import AnthropicMessageRecord, AnthropicMessagesDocument
from e2h.gemini_generate_content import GeminiGenerateContentDocument, GeminiGenerateContentRecord
from e2h.openai_responses import OpenAIResponseRecord, OpenAIResponsesDocument

NOW = datetime(2026, 8, 10, 21, 0, tzinfo=UTC)


def test_openai_document_revalidates_mutated_record_field_constraints() -> None:
    record = OpenAIResponseRecord(
        response={
            "id": "response",
            "object": "response",
            "created_at": 1,
            "model": "model",
            "output": [],
        },
        request_id="request",
    )
    record.request_id = "x" * 256

    with pytest.raises(ValidationError) as exc_info:
        OpenAIResponsesDocument(id="archive", responses=[record])

    assert exc_info.value.errors()[0]["loc"][-1] == "request_id"


def test_anthropic_document_revalidates_mutated_timestamp() -> None:
    record = AnthropicMessageRecord(
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
    record.timestamp = NOW.replace(tzinfo=None)

    with pytest.raises(ValidationError, match="timestamp must be timezone-aware"):
        AnthropicMessagesDocument(id="archive", records=[record])


def test_gemini_document_revalidates_mutated_timestamp() -> None:
    record = GeminiGenerateContentRecord(
        timestamp=NOW,
        response={"responseId": "response", "candidates": []},
        candidate_ids=[],
    )
    record.timestamp = NOW.replace(tzinfo=None)

    with pytest.raises(ValidationError, match="timestamp must be timezone-aware"):
        GeminiGenerateContentDocument(id="archive", records=[record])
