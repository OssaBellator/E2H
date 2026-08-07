from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from e2h.capture import (
    CaptureClient,
    CaptureDocument,
    CaptureError,
    CaptureKind,
    CaptureObservation,
    CaptureSource,
    capture_content_sha256,
    capture_document_sha256,
    load_capture_document,
)
from e2h.capture_cli import capture_app

runner = CliRunner()


def _observation(
    *,
    client: CaptureClient = CaptureClient.BROWSER,
    captured_at: datetime | None = None,
    content: str = "observable selection",
    identifier: str = "selection-1",
) -> CaptureObservation:
    kind = (
        CaptureKind.BROWSER_SELECTION
        if client is CaptureClient.BROWSER
        else CaptureKind.VSCODE_SELECTION
    )
    return CaptureObservation(
        id=identifier,
        kind=kind,
        captured_at=captured_at or datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
        content=content,
        content_sha256=capture_content_sha256(content),
        source=CaptureSource(label="Example", locator="https://example.com"),
        metadata={"capture_mode": "manual-selection"},
    )


def _document(*, client: CaptureClient = CaptureClient.BROWSER) -> CaptureDocument:
    captured_at = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
    return CaptureDocument(
        id=f"{client.value}-capture-1",
        client=client,
        captured_at=captured_at,
        observations=[_observation(client=client, captured_at=captured_at)],
        metadata={"manual": True},
    )


def test_capture_content_digest_matches_exact_utf8() -> None:
    content = "observable π selection\n"
    assert capture_content_sha256(content) == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_capture_document_digest_is_stable() -> None:
    first = _document()
    second = CaptureDocument.model_validate(first.model_dump(mode="json"))
    assert capture_document_sha256(first) == capture_document_sha256(second)
    assert len(capture_document_sha256(first)) == 64


def test_observation_rejects_modified_content() -> None:
    with pytest.raises(ValidationError, match="content_sha256"):
        CaptureObservation(
            id="selection-1",
            kind=CaptureKind.BROWSER_SELECTION,
            captured_at=datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
            content="modified",
            content_sha256="0" * 64,
            source=CaptureSource(label="Example", locator="https://example.com"),
        )


def test_document_rejects_kind_for_other_client() -> None:
    observation = _observation(client=CaptureClient.VSCODE)
    with pytest.raises(ValidationError, match="kind does not match capture client"):
        CaptureDocument(
            id="browser-capture-1",
            client=CaptureClient.BROWSER,
            captured_at=observation.captured_at,
            observations=[observation],
        )


def test_document_rejects_duplicate_ids_and_unordered_timestamps() -> None:
    first_at = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)
    second_at = first_at + timedelta(seconds=1)
    duplicate = _observation(captured_at=second_at, identifier="same")
    with pytest.raises(ValidationError, match="observation ids must be unique"):
        CaptureDocument(
            id="browser-capture-1",
            client=CaptureClient.BROWSER,
            captured_at=first_at,
            observations=[_observation(captured_at=first_at, identifier="same"), duplicate],
        )

    with pytest.raises(ValidationError, match="timestamps must be nondecreasing"):
        CaptureDocument(
            id="browser-capture-2",
            client=CaptureClient.BROWSER,
            captured_at=first_at,
            observations=[
                _observation(captured_at=second_at, identifier="later"),
                _observation(captured_at=first_at, identifier="earlier"),
            ],
        )


def test_capture_timestamps_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _observation(captured_at=datetime(2026, 8, 7, 9, 0))
    with pytest.raises(ValidationError, match="timezone-aware"):
        CaptureDocument(
            id="browser-capture-1",
            client=CaptureClient.BROWSER,
            captured_at=datetime(2026, 8, 7, 9, 0),
            observations=[_observation()],
        )


def test_load_capture_document_verifies_file(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    document = _document()
    path.write_text(document.model_dump_json(indent=2) + "\n", encoding="utf-8")
    loaded = load_capture_document(path)
    assert loaded == document

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["observations"][0]["content"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CaptureError, match="content_sha256"):
        load_capture_document(path)


def test_load_capture_document_rejects_non_object_and_nonstandard_constants(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(CaptureError, match="root must be an object"):
        load_capture_document(path)
    path.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(CaptureError, match="non-standard JSON constant"):
        load_capture_document(path)


def test_capture_cli_validate_and_inspect_do_not_echo_content(tmp_path: Path) -> None:
    path = tmp_path / "capture.json"
    document = _document()
    path.write_text(document.model_dump_json(indent=2) + "\n", encoding="utf-8")

    validated = runner.invoke(capture_app, ["validate", str(path), "--json"])
    assert validated.exit_code == 0
    validation = json.loads(validated.stdout)
    assert validation["valid"] is True
    assert validation["client"] == "browser"
    assert validation["sha256"] == capture_document_sha256(document)

    inspected = runner.invoke(capture_app, ["inspect", str(path), "--json"])
    assert inspected.exit_code == 0
    assert "observable selection" not in inspected.stdout
    inspection = json.loads(inspected.stdout)
    assert inspection["observation_count"] == 1
    assert inspection["content_sha256"] == [document.observations[0].content_sha256]


def test_capture_cli_schema_contains_client_and_observations() -> None:
    result = runner.invoke(capture_app, ["schema"])
    assert result.exit_code == 0
    schema = json.loads(result.stdout)
    assert "client" in schema["properties"]
    assert "observations" in schema["properties"]
