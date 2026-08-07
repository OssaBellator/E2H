"""Portable, content-addressed evidence captures from interactive clients."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_CAPTURE_BYTES = 10 * 1024 * 1024
_MAX_OBSERVATIONS = 1_000
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,255}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class CaptureError(ValueError):
    """Raised when a capture document is invalid or cannot be loaded safely."""


class CaptureClient(StrEnum):
    """First-party clients that emit the portable capture envelope."""

    BROWSER = "browser"
    VSCODE = "vscode"


class CaptureKind(StrEnum):
    """Observable capture operations supported by the first-party clients."""

    BROWSER_SELECTION = "browser.selection"
    VSCODE_SELECTION = "vscode.selection"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _ensure_json(value: Any, noun: str) -> None:
    try:
        json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{noun} must be JSON-serializable") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _aware(value: datetime, noun: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{noun} must be timezone-aware")
    return value


def capture_content_sha256(content: str) -> str:
    """Return the SHA-256 of the exact captured UTF-8 content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class CaptureSource(StrictModel):
    """User-visible resource hint without requiring a local absolute filesystem path."""

    label: str = Field(min_length=1, max_length=255)
    locator: str = Field(min_length=1, max_length=4096)


class CaptureObservation(StrictModel):
    """One explicitly captured, observable selection."""

    id: str = Field(pattern=_ID_PATTERN)
    kind: CaptureKind
    captured_at: datetime
    content: str = Field(min_length=1, max_length=1_000_000)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    source: CaptureSource
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value, "observation captured_at")

    @model_validator(mode="after")
    def content_digest_and_metadata_must_match(self) -> CaptureObservation:
        if self.content_sha256 != capture_content_sha256(self.content):
            raise ValueError("content_sha256 does not match captured content")
        _ensure_json(self.metadata, "observation metadata")
        return self


class CaptureDocument(StrictModel):
    """Portable manual-capture envelope shared by browser and VS Code clients."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    capsule_id: str = Field(default="unassigned", min_length=1, max_length=255)
    client: CaptureClient
    captured_at: datetime
    observations: list[CaptureObservation] = Field(
        min_length=1,
        max_length=_MAX_OBSERVATIONS,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_aware(cls, value: datetime) -> datetime:
        return _aware(value, "document captured_at")

    @model_validator(mode="after")
    def observations_must_be_consistent(self) -> CaptureDocument:
        _ensure_json(self.metadata, "capture metadata")
        seen: set[str] = set()
        previous: datetime | None = None
        expected_prefix = f"{self.client.value}."
        for observation in self.observations:
            if observation.id in seen:
                raise ValueError("observation ids must be unique")
            if previous is not None and observation.captured_at < previous:
                raise ValueError("observation timestamps must be nondecreasing")
            if not observation.kind.value.startswith(expected_prefix):
                raise ValueError("observation kind does not match capture client")
            seen.add(observation.id)
            previous = observation.captured_at
        return self


def capture_document_sha256(document: CaptureDocument) -> str:
    """Bind the exact normalized capture document to a canonical SHA-256."""
    rendered = json.dumps(
        document.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def load_capture_document(path: Path) -> CaptureDocument:
    """Load one bounded UTF-8 JSON capture document and verify all content digests."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CaptureError(f"unable to read capture: {exc}") from exc
    if len(raw) > _MAX_CAPTURE_BYTES:
        raise CaptureError(f"capture exceeds {_MAX_CAPTURE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CaptureError("capture must be UTF-8") from exc
    try:
        payload = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CaptureError(f"invalid capture JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CaptureError("capture root must be an object")
    try:
        return CaptureDocument.model_validate(payload)
    except ValueError as exc:
        raise CaptureError(str(exc)) from exc
