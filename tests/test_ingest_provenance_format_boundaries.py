from __future__ import annotations

from datetime import UTC, datetime

import pytest

from e2h.ingest import (
    EvidenceFormat,
    EvidenceIngestError,
    SourceProvenance,
    TranscriptDocument,
    TranscriptMessage,
    import_otlp_data,
    import_transcript_document,
)

NOW = datetime(2026, 8, 10, 0, 30, tzinfo=UTC)


def _document() -> TranscriptDocument:
    return TranscriptDocument(
        id="trace",
        messages=[
            TranscriptMessage(
                id="message",
                role="user",
                content="hello",
                timestamp=NOW,
            )
        ],
    )


def _provenance(source_format: EvidenceFormat) -> SourceProvenance:
    return SourceProvenance(
        format=source_format,
        source_name="source.json",
        sha256="a" * 64,
        size_bytes=1,
        redaction_enabled=True,
    )


@pytest.mark.parametrize(
    "source_format",
    [
        EvidenceFormat.OTLP_JSON,
        EvidenceFormat.OPENAI_RESPONSES_JSON,
        EvidenceFormat.ANTHROPIC_MESSAGES_JSON,
        EvidenceFormat.GEMINI_GENERATE_CONTENT_JSON,
    ],
)
def test_transcript_import_requires_transcript_provenance(
    source_format: EvidenceFormat,
) -> None:
    with pytest.raises(
        EvidenceIngestError,
        match="expected format 'transcript-json'",
    ):
        import_transcript_document(_document(), _provenance(source_format))


@pytest.mark.parametrize(
    "source_format",
    [
        EvidenceFormat.TRANSCRIPT_JSON,
        EvidenceFormat.OPENAI_RESPONSES_JSON,
        EvidenceFormat.ANTHROPIC_MESSAGES_JSON,
        EvidenceFormat.GEMINI_GENERATE_CONTENT_JSON,
    ],
)
def test_otlp_import_requires_otlp_provenance_before_parsing(
    source_format: EvidenceFormat,
) -> None:
    with pytest.raises(
        EvidenceIngestError,
        match="expected format 'otlp-json'",
    ):
        import_otlp_data({"resourceSpans": []}, _provenance(source_format))
