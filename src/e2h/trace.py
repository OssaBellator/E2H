"""Normalized observable trace records for E2H executions."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.atomic_write import write_text_atomic
from e2h.runner import RunResult


class TraceEventType(StrEnum):
    """Event names emitted by the deterministic replay adapter."""

    CONVERSATION_STARTED = "conversation.started"
    MESSAGE_OBSERVED = "message.observed"
    TOOL_CALLED = "tool.called"
    TOOL_COMPLETED = "tool.completed"
    ARTIFACT_OBSERVED = "artifact.observed"
    FEEDBACK_OBSERVED = "feedback.observed"
    SPAN_STARTED = "span.started"
    SPAN_EVENT_OBSERVED = "span.event.observed"
    SPAN_COMPLETED = "span.completed"
    RUN_STARTED = "run.started"
    CHECK_COMPLETED = "check.completed"
    RUN_COMPLETED = "run.completed"


def _validate_python_json_values(value: Any) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("trace event contains a non-finite number")
        return
    if isinstance(value, (set, frozenset)):
        raise ValueError("trace event contains an unordered set")
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_python_json_values(key)
            _validate_python_json_values(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_python_json_values(item)


class TraceContext(BaseModel):
    """Stable identifiers that connect evidence to an experiment slot."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,255}$")
    capsule_id: str
    experiment_id: str | None = None
    variant_id: str | None = None
    repetition: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceEvent(BaseModel):
    """One JSON-serializable, observable event in a normalized trace."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    trace_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,255}$")
    sequence: int = Field(ge=0)
    event_type: TraceEventType
    timestamp: datetime
    context: TraceContext
    attributes: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def payload_must_be_json_serializable(self) -> TraceEvent:
        try:
            _validate_python_json_values(self.model_dump(mode="python"))
            json.dumps(self.model_dump(mode="json"), sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("trace event must be JSON-serializable") from exc
        return self


class Trace(BaseModel):
    """A validated sequence of events for one execution."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    trace_id: str
    events: list[TraceEvent] = Field(min_length=2)

    @model_validator(mode="after")
    def events_must_form_one_ordered_trace(self) -> Trace:
        expected_sequences = list(range(len(self.events)))
        if [event.sequence for event in self.events] != expected_sequences:
            raise ValueError("trace event sequences must be contiguous and start at zero")
        if any(event.trace_id != self.trace_id for event in self.events):
            raise ValueError("all events must use the trace trace_id")
        if any(
            current.timestamp < previous.timestamp
            for previous, current in zip(self.events, self.events[1:], strict=False)
        ):
            raise ValueError("trace event timestamps must be nondecreasing")
        return self


def _revalidate_trace_for_write(value: Trace) -> Trace:
    if type(value) is not Trace:
        raise ValueError(f"invalid trace: expected Trace, got {type(value).__name__}")
    try:
        payload = value.model_dump(mode="python", warnings="none")
        _validate_python_json_values(payload)
        return Trace.model_validate(payload)
    except ValueError as exc:
        raise ValueError(f"invalid trace: {exc}") from exc


def trace_from_run_result(
    result: RunResult,
    *,
    run_id: str,
    experiment_id: str | None = None,
    variant_id: str | None = None,
    repetition: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> Trace:
    """Convert an observable replay result into a normalized event trace."""
    context = TraceContext(
        run_id=run_id,
        capsule_id=result.capsule_id,
        experiment_id=experiment_id,
        variant_id=variant_id,
        repetition=repetition,
        metadata=metadata or {},
    )
    events = [
        TraceEvent(
            trace_id=run_id,
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=result.started_at,
            context=context,
            payload={"capsule_id": result.capsule_id},
        )
    ]

    elapsed = 0.0
    for check in result.checks:
        elapsed += check.duration_seconds
        timestamp = min(result.started_at + timedelta(seconds=elapsed), result.finished_at)
        events.append(
            TraceEvent(
                trace_id=run_id,
                sequence=len(events),
                event_type=TraceEventType.CHECK_COMPLETED,
                timestamp=timestamp,
                context=context,
                attributes={"timestamp_source": "derived_from_check_durations"},
                payload=check.model_dump(mode="json"),
            )
        )

    events.append(
        TraceEvent(
            trace_id=run_id,
            sequence=len(events),
            event_type=TraceEventType.RUN_COMPLETED,
            timestamp=result.finished_at,
            context=context,
            payload={
                "status": result.status.value,
                "duration_seconds": result.duration_seconds,
                "check_count": len(result.checks),
                "failure_summary": result.failure_summary.model_dump(mode="json"),
            },
        )
    )
    return Trace(trace_id=run_id, events=events)


def write_json_atomic(path: Path, payload: str) -> None:
    """Atomically replace a UTF-8 text file on the same filesystem."""
    write_text_atomic(path, payload)


def write_traces_jsonl(path: Path, traces: list[Trace]) -> None:
    """Write normalized events as deterministic JSON Lines."""
    lines: list[str] = []
    for trace in traces:
        normalized = _revalidate_trace_for_write(trace)
        for event in normalized.events:
            lines.append(
                json.dumps(
                    event.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
    write_json_atomic(path, "\n".join(lines) + ("\n" if lines else ""))
