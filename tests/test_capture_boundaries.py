"""Regression coverage for capture content-addressing and exact JSON boundaries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from e2h.capture import (
    CaptureClient,
    CaptureDocument,
    CaptureError,
    CaptureKind,
    CaptureObservation,
    CaptureSource,
    capture_content_sha256,
    capture_document_sha256,
)

NOW = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)


def _document() -> CaptureDocument:
    content = "captured text"
    return CaptureDocument(
        id="capture-1",
        client=CaptureClient.BROWSER,
        captured_at=NOW,
        observations=[
            CaptureObservation(
                id="selection-1",
                kind=CaptureKind.BROWSER_SELECTION,
                captured_at=NOW,
                content=content,
                content_sha256=capture_content_sha256(content),
                source=CaptureSource(label="Example", locator="https://example.com"),
            )
        ],
    )


def test_capture_metadata_rejects_python_only_tuple() -> None:
    with pytest.raises(ValidationError, match="canonical JSON"):
        CaptureDocument(
            id="capture-1",
            client=CaptureClient.BROWSER,
            captured_at=NOW,
            observations=_document().observations,
            metadata={"values": (1, 2)},
        )


def test_capture_observation_metadata_rejects_python_only_tuple() -> None:
    content = "captured text"
    with pytest.raises(ValidationError, match="canonical JSON"):
        CaptureObservation(
            id="selection-1",
            kind=CaptureKind.BROWSER_SELECTION,
            captured_at=NOW,
            content=content,
            content_sha256=capture_content_sha256(content),
            source=CaptureSource(label="Example", locator="https://example.com"),
            metadata={"values": (1, 2)},
        )


def test_capture_digest_revalidates_mutated_document() -> None:
    document = _document()
    document.observations[0].content = "mutated"

    with pytest.raises(CaptureError, match="invalid capture document"):
        capture_document_sha256(document)


def test_capture_digest_rejects_document_subclass() -> None:
    class CaptureSubclass(CaptureDocument):
        pass

    payload = _document().model_dump(mode="python")
    document = CaptureSubclass.model_validate(payload)

    with pytest.raises(CaptureError, match="expected CaptureDocument"):
        capture_document_sha256(document)


def test_capture_digest_preserves_valid_identity() -> None:
    document = _document()
    detached = CaptureDocument.model_validate(document.model_dump(mode="python"))

    assert capture_document_sha256(document) == capture_document_sha256(detached)
