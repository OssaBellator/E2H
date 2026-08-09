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

NOW = datetime(2026, 8, 9, 15, 30, tzinfo=UTC)


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


def test_transcript_import_revalidates_mutated_message_role() -> None:
    document = _document()
    document.messages[0].role = "broken"  # type: ignore[assignment]

    with pytest.raises(EvidenceIngestError, match="invalid transcript document"):
        import_transcript_document(
            document,
            _provenance(EvidenceFormat.TRANSCRIPT_JSON),
        )


def test_transcript_import_revalidates_mutated_metadata() -> None:
    document = _document()
    document.metadata["nested"] = (1, 2)

    with pytest.raises(EvidenceIngestError, match="invalid transcript document"):
        import_transcript_document(
            document,
            _provenance(EvidenceFormat.TRANSCRIPT_JSON),
        )


def test_transcript_import_revalidates_mutated_provenance() -> None:
    provenance = _provenance(EvidenceFormat.TRANSCRIPT_JSON)
    provenance.sha256 = "broken"

    with pytest.raises(EvidenceIngestError, match="invalid source provenance"):
        import_transcript_document(_document(), provenance)


def test_otlp_import_revalidates_provenance_before_parsing_data() -> None:
    provenance = _provenance(EvidenceFormat.OTLP_JSON)
    provenance.sha256 = "broken"

    with pytest.raises(EvidenceIngestError, match="invalid source provenance"):
        import_otlp_data({"resourceSpans": []}, provenance)


def test_transcript_import_rejects_model_subclasses() -> None:
    class TranscriptSubclass(TranscriptDocument):
        pass

    document = TranscriptSubclass.model_validate(_document().model_dump(mode="python"))

    with pytest.raises(EvidenceIngestError, match="expected TranscriptDocument"):
        import_transcript_document(
            document,
            _provenance(EvidenceFormat.TRANSCRIPT_JSON),
        )
