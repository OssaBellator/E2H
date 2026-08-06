"""Privacy-aware importers for transcript and OpenTelemetry evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.privacy import (
    RedactionKind as RedactionKind,
)
from e2h.privacy import (
    RedactionOutcome,
    RedactionPolicy,
    RedactionPolicyError,
    RedactionReview,
    apply_redaction_policy,
    default_redaction_policy,
    redaction_policy_sha256,
)
from e2h.privacy import (
    RedactionRecord as RedactionRecord,
)
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

_MAX_SOURCE_BYTES = 10 * 1024 * 1024
_MAX_RECORDS = 10_000
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,255}$"
_HEX_TRACE_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_HEX_SPAN_ID = re.compile(r"^[0-9a-fA-F]{16}$")
_INTEGER_TEXT = re.compile(r"^-?\d+$")


class EvidenceIngestError(ValueError):
    """Raised when observable evidence cannot be safely normalized."""


class EvidenceFormat(StrEnum):
    """Supported source document formats."""

    TRANSCRIPT_JSON = "transcript-json"
    OTLP_JSON = "otlp-json"
    OPENAI_RESPONSES_JSON = "openai-responses-json"
    ANTHROPIC_MESSAGES_JSON = "anthropic-messages-json"


class TranscriptRole(StrEnum):
    """Roles accepted by the canonical transcript importer."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceProvenance(StrictModel):
    """Content-addressed source identity without exposing a local filesystem path."""

    format: EvidenceFormat
    source_name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0, le=_MAX_SOURCE_BYTES)
    redaction_enabled: bool
    redaction_policy_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    redaction_policy_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class CorrectionRecord(StrictModel):
    """Explicit user correction linked to the earlier assistant message it replaces."""

    trace_id: str
    message_id: str
    correction_of: str
    event_sequence: int = Field(ge=0)


class IngestionBundle(StrictModel):
    """Normalized evidence plus provenance and privacy records."""

    schema_version: Literal["0.1"] = "0.1"
    provenance: SourceProvenance
    traces: list[Trace] = Field(min_length=1)
    corrections: list[CorrectionRecord] = Field(default_factory=list)
    redactions: list[RedactionRecord] = Field(default_factory=list)
    redaction_review: RedactionReview | None = None


class TranscriptMessage(StrictModel):
    """One observable message from a canonical transcript document."""

    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
    role: TranscriptRole
    content: str = Field(min_length=1, max_length=1_000_000)
    timestamp: datetime
    name: str | None = Field(default=None, max_length=255)
    tool_call_id: str | None = Field(default=None, max_length=255)
    correction_of: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def metadata_must_be_json_serializable(self) -> TranscriptMessage:
        _ensure_json(self.metadata, "message metadata")
        return self


class TranscriptDocument(StrictModel):
    """Portable source format for visible web-chat and API conversations."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    capsule_id: str = Field(default="unassigned", min_length=1, max_length=255)
    messages: list[TranscriptMessage] = Field(min_length=1, max_length=_MAX_RECORDS)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def messages_must_be_ordered_and_corrections_valid(self) -> TranscriptDocument:
        _ensure_json(self.metadata, "transcript metadata")
        seen: dict[str, TranscriptMessage] = {}
        previous_timestamp: datetime | None = None
        for message in self.messages:
            if message.id in seen:
                raise ValueError("message ids must be unique")
            if previous_timestamp is not None and message.timestamp < previous_timestamp:
                raise ValueError("message timestamps must be nondecreasing")
            if message.correction_of is not None:
                target = seen.get(message.correction_of)
                if target is None:
                    raise ValueError("correction_of must reference an earlier message")
                if target.role is not TranscriptRole.ASSISTANT:
                    raise ValueError("correction_of must reference an assistant message")
                if message.role is not TranscriptRole.USER:
                    raise ValueError("only user messages may declare correction_of")
            seen[message.id] = message
            previous_timestamp = message.timestamp
        return self


@dataclass(frozen=True)
class _LoadedSource:
    data: dict[str, Any]
    provenance: SourceProvenance


@dataclass(frozen=True)
class _ParsedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_nanoseconds: int
    end_nanoseconds: int
    start: datetime
    end: datetime
    kind: Any
    status: Any
    attributes: dict[str, Any]
    resource: dict[str, Any]
    scope: dict[str, Any]
    events: list[tuple[int, datetime, str, dict[str, Any]]]


def _ensure_json(value: Any, noun: str) -> None:
    try:
        json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{noun} must be JSON-serializable") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _load_json(
    path: Path,
    source_format: EvidenceFormat,
    redact: bool,
    redaction_policy: RedactionPolicy | None = None,
) -> _LoadedSource:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise EvidenceIngestError(f"unable to read evidence: {exc}") from exc
    if len(raw) > _MAX_SOURCE_BYTES:
        raise EvidenceIngestError(f"evidence exceeds {_MAX_SOURCE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceIngestError("evidence must be UTF-8") from exc
    try:
        data = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise EvidenceIngestError(f"invalid evidence JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EvidenceIngestError("evidence root must be an object")
    active_policy = redaction_policy or default_redaction_policy()
    provenance = SourceProvenance(
        format=source_format,
        source_name=path.name,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        redaction_enabled=redact,
        redaction_policy_id=active_policy.id,
        redaction_policy_sha256=redaction_policy_sha256(active_policy),
    )
    return _LoadedSource(data=data, provenance=provenance)


def _apply_privacy_policy(
    traces: list[Trace],
    provenance: SourceProvenance,
    redaction_policy: RedactionPolicy | None,
) -> RedactionOutcome:
    try:
        return apply_redaction_policy(
            traces,
            policy=redaction_policy,
            redaction_enabled=provenance.redaction_enabled,
            max_records=_MAX_RECORDS,
        )
    except RedactionPolicyError as exc:
        raise EvidenceIngestError(str(exc)) from exc


def import_transcript_document(
    document: TranscriptDocument,
    provenance: SourceProvenance,
    *,
    capsule_id: str | None = None,
    redaction_policy: RedactionPolicy | None = None,
) -> IngestionBundle:
    """Normalize a validated canonical transcript into observable trace events."""
    selected_capsule = capsule_id or document.capsule_id
    context = TraceContext(
        run_id=document.id,
        capsule_id=selected_capsule,
        metadata={**document.metadata, "source_format": EvidenceFormat.TRANSCRIPT_JSON.value},
    )
    first_timestamp = document.messages[0].timestamp
    events = [
        TraceEvent(
            trace_id=document.id,
            sequence=0,
            event_type=TraceEventType.CONVERSATION_STARTED,
            timestamp=first_timestamp,
            context=context,
            payload={"conversation_id": document.id, "message_count": len(document.messages)},
        )
    ]
    corrections: list[CorrectionRecord] = []
    for message in document.messages:
        events.append(
            TraceEvent(
                trace_id=document.id,
                sequence=len(events),
                event_type=TraceEventType.MESSAGE_OBSERVED,
                timestamp=message.timestamp,
                context=context,
                attributes={"message_id": message.id, "role": message.role.value},
                payload={
                    "content": message.content,
                    "name": message.name,
                    "tool_call_id": message.tool_call_id,
                    "metadata": message.metadata,
                },
            )
        )
        if message.correction_of is not None:
            feedback_sequence = len(events)
            events.append(
                TraceEvent(
                    trace_id=document.id,
                    sequence=feedback_sequence,
                    event_type=TraceEventType.FEEDBACK_OBSERVED,
                    timestamp=message.timestamp,
                    context=context,
                    attributes={"feedback_kind": "explicit_correction"},
                    payload={
                        "message_id": message.id,
                        "correction_of": message.correction_of,
                    },
                )
            )
            corrections.append(
                CorrectionRecord(
                    trace_id=document.id,
                    message_id=message.id,
                    correction_of=message.correction_of,
                    event_sequence=feedback_sequence,
                )
            )

    outcome = _apply_privacy_policy(
        [Trace(trace_id=document.id, events=events)],
        provenance,
        redaction_policy,
    )
    return IngestionBundle(
        provenance=provenance,
        traces=outcome.traces,
        corrections=corrections,
        redactions=outcome.records,
        redaction_review=outcome.review,
    )


def ingest_transcript_file(
    path: Path,
    *,
    capsule_id: str | None = None,
    redact: bool = True,
    redaction_policy: RedactionPolicy | None = None,
) -> IngestionBundle:
    """Load and normalize a canonical transcript JSON file."""
    source = _load_json(path, EvidenceFormat.TRANSCRIPT_JSON, redact, redaction_policy)
    try:
        document = TranscriptDocument.model_validate(source.data)
        return import_transcript_document(
            document,
            source.provenance,
            capsule_id=capsule_id,
            redaction_policy=redaction_policy,
        )
    except ValueError as exc:
        if isinstance(exc, EvidenceIngestError):
            raise
        raise EvidenceIngestError(str(exc)) from exc


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceIngestError(f"{location} must be an object")
    return cast(dict[str, Any], value)


def _list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceIngestError(f"{location} must be an array")
    return value


def _text(value: Any, location: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise EvidenceIngestError(f"{location} must be a string")
    return value


def _integer(value: Any, location: str) -> int:
    if isinstance(value, bool):
        raise EvidenceIngestError(f"{location} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER_TEXT.fullmatch(value) is not None:
        return int(value)
    raise EvidenceIngestError(f"{location} must be an integer")


def _nanoseconds(value: Any, location: str) -> int:
    nanoseconds = _integer(value, location)
    if nanoseconds < 0:
        raise EvidenceIngestError(f"{location} must be non-negative")
    return nanoseconds


def _datetime_from_nanoseconds(nanoseconds: int, location: str) -> datetime:
    try:
        return datetime.fromtimestamp(nanoseconds / 1_000_000_000, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise EvidenceIngestError(f"{location} is outside the supported timestamp range") from exc


def _unix_nano(value: Any, location: str) -> tuple[int, datetime]:
    nanoseconds = _nanoseconds(value, location)
    return nanoseconds, _datetime_from_nanoseconds(nanoseconds, location)


def _decode_any_value(value: Any, location: str) -> Any:
    mapping = _mapping(value, location)
    known = [
        key
        for key in (
            "stringValue",
            "boolValue",
            "intValue",
            "doubleValue",
            "bytesValue",
            "arrayValue",
            "kvlistValue",
        )
        if key in mapping
    ]
    if len(known) != 1:
        raise EvidenceIngestError(f"{location} must contain exactly one OTLP value field")
    key = known[0]
    raw = mapping[key]
    if key == "stringValue" or key == "bytesValue":
        return _text(raw, f"{location}/{key}", allow_empty=True)
    if key == "boolValue":
        if not isinstance(raw, bool):
            raise EvidenceIngestError(f"{location}/{key} must be a boolean")
        return raw
    if key == "intValue":
        integer_result = _integer(raw, f"{location}/{key}")
        if integer_result < _INT64_MIN or integer_result > _INT64_MAX:
            raise EvidenceIngestError(f"{location}/{key} must fit a signed 64-bit integer")
        return integer_result
    if key == "doubleValue":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise EvidenceIngestError(f"{location}/{key} must be a number")
        double_result = float(raw)
        if not math.isfinite(double_result):
            raise EvidenceIngestError(f"{location}/{key} must be finite")
        return double_result
    if key == "arrayValue":
        array = _mapping(raw, f"{location}/{key}")
        values = _list(array.get("values", []), f"{location}/{key}/values")
        return [
            _decode_any_value(item, f"{location}/{key}/values/{index}")
            for index, item in enumerate(values)
        ]
    key_values = _mapping(raw, f"{location}/{key}")
    return _decode_attributes(key_values.get("values", []), f"{location}/{key}/values")


def _decode_attributes(value: Any, location: str) -> dict[str, Any]:
    items = _list(value, location)
    result: dict[str, Any] = {}
    for index, item in enumerate(items):
        mapping = _mapping(item, f"{location}/{index}")
        key = _text(mapping.get("key"), f"{location}/{index}/key")
        if key in result:
            raise EvidenceIngestError(f"duplicate OTLP attribute key: {key}")
        result[key] = _decode_any_value(mapping.get("value"), f"{location}/{index}/value")
    return result


def _parse_span(
    raw: Any,
    *,
    resource: dict[str, Any],
    scope: dict[str, Any],
    location: str,
) -> _ParsedSpan:
    span = _mapping(raw, location)
    trace_id = _text(span.get("traceId"), f"{location}/traceId")
    span_id = _text(span.get("spanId"), f"{location}/spanId")
    if _HEX_TRACE_ID.fullmatch(trace_id) is None:
        raise EvidenceIngestError(f"{location}/traceId must be 32 hexadecimal characters")
    if int(trace_id, 16) == 0:
        raise EvidenceIngestError(f"{location}/traceId must not be all zeros")
    if _HEX_SPAN_ID.fullmatch(span_id) is None:
        raise EvidenceIngestError(f"{location}/spanId must be 16 hexadecimal characters")
    if int(span_id, 16) == 0:
        raise EvidenceIngestError(f"{location}/spanId must not be all zeros")
    parent_raw = span.get("parentSpanId")
    parent_span_id = None
    if parent_raw not in (None, ""):
        parent_span_id = _text(parent_raw, f"{location}/parentSpanId")
        if _HEX_SPAN_ID.fullmatch(parent_span_id) is None:
            raise EvidenceIngestError(f"{location}/parentSpanId must be 16 hexadecimal characters")
        if int(parent_span_id, 16) == 0:
            raise EvidenceIngestError(f"{location}/parentSpanId must not be all zeros")
    start_nanoseconds, start = _unix_nano(
        span.get("startTimeUnixNano"), f"{location}/startTimeUnixNano"
    )
    end_nanoseconds, end = _unix_nano(span.get("endTimeUnixNano"), f"{location}/endTimeUnixNano")
    if end_nanoseconds < start_nanoseconds:
        raise EvidenceIngestError(f"{location} ends before it starts")
    events: list[tuple[int, datetime, str, dict[str, Any]]] = []
    for index, raw_event in enumerate(_list(span.get("events", []), f"{location}/events")):
        event = _mapping(raw_event, f"{location}/events/{index}")
        event_nanoseconds, event_time = _unix_nano(
            event.get("timeUnixNano"), f"{location}/events/{index}/timeUnixNano"
        )
        events.append(
            (
                event_nanoseconds,
                event_time,
                _text(event.get("name"), f"{location}/events/{index}/name"),
                _decode_attributes(
                    event.get("attributes", []), f"{location}/events/{index}/attributes"
                ),
            )
        )
    return _ParsedSpan(
        trace_id=trace_id.lower(),
        span_id=span_id.lower(),
        parent_span_id=parent_span_id.lower() if parent_span_id else None,
        name=_text(span.get("name"), f"{location}/name"),
        start_nanoseconds=start_nanoseconds,
        end_nanoseconds=end_nanoseconds,
        start=start,
        end=end,
        kind=span.get("kind"),
        status=span.get("status", {}),
        attributes=_decode_attributes(span.get("attributes", []), f"{location}/attributes"),
        resource=resource,
        scope=scope,
        events=events,
    )


def _parse_otlp(data: dict[str, Any]) -> dict[str, list[_ParsedSpan]]:
    resource_spans = _list(data.get("resourceSpans"), "/resourceSpans")
    parsed: dict[str, list[_ParsedSpan]] = defaultdict(list)
    seen_span_ids: set[tuple[str, str]] = set()
    span_count = 0
    for resource_index, raw_resource_spans in enumerate(resource_spans):
        location = f"/resourceSpans/{resource_index}"
        item = _mapping(raw_resource_spans, location)
        resource_raw = _mapping(item.get("resource", {}), f"{location}/resource")
        resource = {
            "attributes": _decode_attributes(
                resource_raw.get("attributes", []), f"{location}/resource/attributes"
            ),
            "schema_url": item.get("schemaUrl"),
        }
        scope_spans = _list(item.get("scopeSpans", []), f"{location}/scopeSpans")
        for scope_index, raw_scope_spans in enumerate(scope_spans):
            scope_location = f"{location}/scopeSpans/{scope_index}"
            scope_item = _mapping(raw_scope_spans, scope_location)
            scope_raw = _mapping(scope_item.get("scope", {}), f"{scope_location}/scope")
            scope = {
                "name": scope_raw.get("name"),
                "version": scope_raw.get("version"),
                "attributes": _decode_attributes(
                    scope_raw.get("attributes", []), f"{scope_location}/scope/attributes"
                ),
                "schema_url": scope_item.get("schemaUrl"),
            }
            spans = _list(scope_item.get("spans", []), f"{scope_location}/spans")
            if span_count + len(spans) > _MAX_RECORDS:
                raise EvidenceIngestError(f"OTLP export exceeds {_MAX_RECORDS} spans")
            for span_index, raw_span in enumerate(spans):
                span_count += 1
                if span_count > _MAX_RECORDS:
                    raise EvidenceIngestError(f"OTLP export exceeds {_MAX_RECORDS} spans")
                span_location = f"{scope_location}/spans/{span_index}"
                parsed_span = _parse_span(
                    raw_span,
                    resource=resource,
                    scope=scope,
                    location=span_location,
                )
                span_key = (parsed_span.trace_id, parsed_span.span_id)
                if span_key in seen_span_ids:
                    raise EvidenceIngestError(
                        f"{span_location}/spanId duplicates an earlier span in the trace"
                    )
                seen_span_ids.add(span_key)
                parsed[parsed_span.trace_id].append(parsed_span)
    if not parsed:
        raise EvidenceIngestError("OTLP export contains no spans")
    return parsed


def _trace_from_spans(trace_id: str, spans: list[_ParsedSpan], capsule_id: str) -> Trace:
    context = TraceContext(
        run_id=trace_id,
        capsule_id=capsule_id,
        metadata={"source_format": EvidenceFormat.OTLP_JSON.value},
    )
    drafts: list[
        tuple[int, datetime, int, str, TraceEventType, dict[str, Any], dict[str, Any]]
    ] = []
    for span in spans:
        common_attributes = {
            "span_attributes": span.attributes,
            "resource": span.resource,
            "scope": span.scope,
        }
        drafts.append(
            (
                span.start_nanoseconds,
                span.start,
                0,
                span.span_id,
                TraceEventType.SPAN_STARTED,
                common_attributes,
                {
                    "span_id": span.span_id,
                    "parent_span_id": span.parent_span_id,
                    "name": span.name,
                    "kind": span.kind,
                },
            )
        )
        for event_nanoseconds, event_time, event_name, event_attributes in span.events:
            drafts.append(
                (
                    event_nanoseconds,
                    event_time,
                    1,
                    span.span_id,
                    TraceEventType.SPAN_EVENT_OBSERVED,
                    {**common_attributes, "event_attributes": event_attributes},
                    {"span_id": span.span_id, "name": event_name},
                )
            )
        drafts.append(
            (
                span.end_nanoseconds,
                span.end,
                2,
                span.span_id,
                TraceEventType.SPAN_COMPLETED,
                common_attributes,
                {
                    "span_id": span.span_id,
                    "name": span.name,
                    "status": span.status,
                    "duration_nanoseconds": span.end_nanoseconds - span.start_nanoseconds,
                },
            )
        )
    drafts.sort(key=lambda item: (item[0], item[2], item[3]))
    events = [
        TraceEvent(
            trace_id=trace_id,
            sequence=sequence,
            event_type=event_type,
            timestamp=timestamp,
            context=context,
            attributes=attributes,
            payload=payload,
        )
        for sequence, (_, timestamp, _, _, event_type, attributes, payload) in enumerate(drafts)
    ]
    return Trace(trace_id=trace_id, events=events)


def import_otlp_data(
    data: dict[str, Any],
    provenance: SourceProvenance,
    *,
    capsule_id: str = "unassigned",
    redaction_policy: RedactionPolicy | None = None,
) -> IngestionBundle:
    """Normalize an OTLP/HTTP JSON trace export into one E2H trace per trace ID."""
    try:
        _ensure_json(data, "OTLP data")
    except ValueError as exc:
        raise EvidenceIngestError(str(exc)) from exc
    traces = [
        _trace_from_spans(trace_id, spans, capsule_id)
        for trace_id, spans in sorted(_parse_otlp(data).items())
    ]
    outcome = _apply_privacy_policy(traces, provenance, redaction_policy)
    return IngestionBundle(
        provenance=provenance,
        traces=outcome.traces,
        redactions=outcome.records,
        redaction_review=outcome.review,
    )


def ingest_otlp_file(
    path: Path,
    *,
    capsule_id: str = "unassigned",
    redact: bool = True,
    redaction_policy: RedactionPolicy | None = None,
) -> IngestionBundle:
    """Load and normalize an OTLP/HTTP JSON trace export."""
    source = _load_json(path, EvidenceFormat.OTLP_JSON, redact, redaction_policy)
    try:
        return import_otlp_data(
            source.data,
            source.provenance,
            capsule_id=capsule_id,
            redaction_policy=redaction_policy,
        )
    except ValueError as exc:
        if isinstance(exc, EvidenceIngestError):
            raise
        raise EvidenceIngestError(str(exc)) from exc
