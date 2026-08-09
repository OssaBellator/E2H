from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.ingest import (
    EvidenceFormat,
    EvidenceIngestError,
    SourceProvenance,
    TranscriptDocument,
    TranscriptMessage,
    import_otlp_data,
    ingest_transcript_file,
)

NOW = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)


def _message(metadata: dict[str, Any] | None = None) -> TranscriptMessage:
    return TranscriptMessage(
        id="message",
        role="user",
        content="hello",
        timestamp=NOW,
        metadata={} if metadata is None else metadata,
    )


def _provenance() -> SourceProvenance:
    return SourceProvenance(
        format=EvidenceFormat.OTLP_JSON,
        source_name="source.json",
        sha256="a" * 64,
        size_bytes=1,
        redaction_enabled=True,
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_transcript_message_rejects_json_coercible_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="message metadata must be JSON-serializable"):
        _message(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_transcript_document_rejects_json_coercible_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="transcript metadata must be JSON-serializable"):
        TranscriptDocument(id="transcript", messages=[_message()], metadata=metadata)


def test_direct_otlp_import_rejects_json_coercible_data() -> None:
    with pytest.raises(EvidenceIngestError, match="OTLP data must be JSON-serializable"):
        import_otlp_data({"resourceSpans": ()}, _provenance())


def test_ingest_rejects_nul_path_before_filesystem_access() -> None:
    with pytest.raises(EvidenceIngestError, match="evidence path must not contain NUL"):
        ingest_transcript_file(Path("bad\x00transcript.json"))


def test_transcript_metadata_preserves_exact_nested_json() -> None:
    metadata = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}
    message = _message(metadata)
    document = TranscriptDocument(id="transcript", messages=[message], metadata=metadata)

    assert message.metadata == metadata
    assert document.metadata == metadata
