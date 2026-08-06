from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from e2h.anthropic_messages import AnthropicMessageRecord


def test_response_content_string_is_rejected() -> None:
    with pytest.raises(ValidationError, match="response.content must be an array"):
        AnthropicMessageRecord.model_validate(
            {
                "timestamp": datetime(2026, 8, 6, 10, 0, tzinfo=UTC),
                "response": {
                    "id": "msg_123",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-test",
                    "content": "plain text",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            }
        )
