from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.anthropic_messages import (
    AnthropicMessageRecord,
    AnthropicMessagesDocument,
)
from e2h.gemini_generate_content import (
    GeminiGenerateContentDocument,
    GeminiGenerateContentRecord,
)
from e2h.openai_responses import (
    OpenAIResponseRecord,
    OpenAIResponsesDocument,
)

NOW = datetime(2026, 8, 10, 1, 0, tzinfo=UTC)


def _openai_record(response_extra: dict[str, Any] | None = None) -> OpenAIResponseRecord:
    response: dict[str, Any] = {
        "id": "response",
        "object": "response",
        "created_at": 1,
        "model": "model",
        "output": [],
    }
    if response_extra:
        response.update(response_extra)
    return OpenAIResponseRecord(response=response)


def _anthropic_record(
    response_extra: dict[str, Any] | None = None,
) -> AnthropicMessageRecord:
    response: dict[str, Any] = {
        "id": "response",
        "type": "message",
        "role": "assistant",
        "model": "model",
        "content": [],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    if response_extra:
        response.update(response_extra)
    return AnthropicMessageRecord(timestamp=NOW, response=response)


def _gemini_record(
    response_extra: dict[str, Any] | None = None,
) -> GeminiGenerateContentRecord:
    response: dict[str, Any] = {
        "responseId": "response",
        "candidates": [],
    }
    if response_extra:
        response.update(response_extra)
    return GeminiGenerateContentRecord(
        timestamp=NOW,
        response=response,
        candidate_ids=[],
    )


@pytest.mark.parametrize(
    ("factory", "extra"),
    [
        (_openai_record, {"metadata": {"nested": (1, 2)}}),
        (_openai_record, {"metadata": {"nested": {1: "coerced key"}}}),
        (_anthropic_record, {"metadata": {"nested": (1, 2)}}),
        (_anthropic_record, {"metadata": {"nested": {1: "coerced key"}}}),
        (_gemini_record, {"metadata": {"nested": (1, 2)}}),
        (_gemini_record, {"metadata": {"nested": {1: "coerced key"}}}),
    ],
)
def test_provider_archive_records_reject_json_coercible_response_values(
    factory: Any,
    extra: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="canonical JSON values"):
        factory(extra)


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": (1, 2)},
        {"nested": {1: "coerced key"}},
    ],
)
def test_openai_document_rejects_json_coercible_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        OpenAIResponsesDocument(id="archive", responses=[_openai_record()], metadata=metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": (1, 2)},
        {"nested": {1: "coerced key"}},
    ],
)
def test_anthropic_document_rejects_json_coercible_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="canonical JSON values"):
        AnthropicMessagesDocument(id="archive", records=[_anthropic_record()], metadata=metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": (1, 2)},
        {"nested": {1: "coerced key"}},
    ],
)
def test_gemini_document_rejects_json_coercible_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="canonical JSON values"):
        GeminiGenerateContentDocument(id="archive", records=[_gemini_record()], metadata=metadata)


def test_provider_archives_preserve_valid_nested_json() -> None:
    metadata = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}

    assert OpenAIResponsesDocument(
        id="openai",
        responses=[_openai_record()],
        metadata=metadata,
    ).metadata == metadata
    assert AnthropicMessagesDocument(
        id="anthropic",
        records=[_anthropic_record()],
        metadata=metadata,
    ).metadata == metadata
    assert GeminiGenerateContentDocument(
        id="gemini",
        records=[_gemini_record()],
        metadata=metadata,
    ).metadata == metadata
