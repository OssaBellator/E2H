from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from e2h.capture import (
    CaptureClient,
    CaptureDocument,
    CaptureKind,
    CaptureObservation,
    CaptureSource,
    capture_content_sha256,
)


def _observation(source: CaptureSource | None = None) -> CaptureObservation:
    content = "selected text"
    return CaptureObservation(
        id="observation",
        kind=CaptureKind.BROWSER_SELECTION,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        content=content,
        content_sha256=capture_content_sha256(content),
        source=source or CaptureSource(label="Page", locator="example.com"),
    )


def test_capture_observation_revalidates_mutated_source() -> None:
    source = CaptureSource(label="Page", locator="example.com")
    source.label = ""

    with pytest.raises(ValidationError) as exc_info:
        _observation(source)

    assert exc_info.value.errors()[0]["loc"][-1] == "label"


def test_capture_document_revalidates_mutated_observation_timestamp() -> None:
    observation = _observation()
    observation.captured_at = datetime(2026, 1, 1)

    with pytest.raises(ValidationError, match="observation captured_at must be timezone-aware"):
        CaptureDocument(
            id="capture",
            client=CaptureClient.BROWSER,
            captured_at=datetime(2026, 1, 1, tzinfo=UTC),
            observations=[observation],
        )
