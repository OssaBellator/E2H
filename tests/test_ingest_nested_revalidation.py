from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from e2h.ingest import TranscriptDocument, TranscriptMessage, TranscriptRole


def _message() -> TranscriptMessage:
    return TranscriptMessage(
        id="message-1",
        role=TranscriptRole.USER,
        content="hello",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_transcript_document_revalidates_mutated_message_timestamp() -> None:
    message = _message()
    message.timestamp = datetime(2026, 1, 1)

    with pytest.raises(ValidationError, match="timestamp must be timezone-aware"):
        TranscriptDocument(id="trace-1", messages=[message])


def test_transcript_document_revalidates_mutated_message_id() -> None:
    message = _message()
    message.id = "invalid message id"

    with pytest.raises(ValidationError) as exc_info:
        TranscriptDocument(id="trace-1", messages=[message])

    assert exc_info.value.errors()[0]["loc"][-1] == "id"
