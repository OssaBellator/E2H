from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType, write_traces_jsonl


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


def _event(sequence: int) -> TraceEvent:
    return TraceEvent(
        trace_id="trace",
        sequence=sequence,
        event_type=TraceEventType.RUN_STARTED,
        timestamp=datetime(2026, 8, 9, 4, 0, tzinfo=UTC) + timedelta(seconds=sequence),
        context=TraceContext(run_id="trace", capsule_id="capsule"),
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_trace_event_rejects_nonfinite_payload(value: float) -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        TraceEvent(
            trace_id="trace",
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=datetime(2026, 8, 9, 4, 0, tzinfo=UTC),
            context=TraceContext(run_id="trace", capsule_id="capsule"),
            payload={"value": value},
        )


@pytest.mark.parametrize("value", [{"alpha", "beta"}, frozenset({"alpha", "beta"})])
def test_trace_event_rejects_unordered_payload(value: object) -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        TraceEvent(
            trace_id="trace",
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=datetime(2026, 8, 9, 4, 0, tzinfo=UTC),
            context=TraceContext(run_id="trace", capsule_id="capsule"),
            payload={"value": value},
        )


def test_trace_event_rejects_nested_non_string_object_key() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        TraceEvent(
            trace_id="trace",
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=datetime(2026, 8, 9, 4, 0, tzinfo=UTC),
            context=TraceContext(run_id="trace", capsule_id="capsule"),
            payload={"nested": {1: "integer", "1": "string"}},
        )


def test_trace_event_rejects_recursive_container() -> None:
    recursive: list[object] = []
    recursive.append(recursive)

    with pytest.raises(ValidationError, match="JSON-serializable"):
        TraceEvent(
            trace_id="trace",
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=datetime(2026, 8, 9, 4, 0, tzinfo=UTC),
            context=TraceContext(run_id="trace", capsule_id="capsule"),
            payload={"recursive": recursive},
        )


def test_trace_event_rejects_nonfinite_context_metadata() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        TraceEvent(
            trace_id="trace",
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=datetime(2026, 8, 9, 4, 0, tzinfo=UTC),
            context=TraceContext(
                run_id="trace", capsule_id="capsule", metadata={"value": float("nan")}
            ),
        )


def test_writer_rejects_nonfinite_value_added_after_validation(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    trace.events[0].payload["value"] = float("nan")
    output = tmp_path / "traces.jsonl"

    with pytest.raises(ValueError, match="non-finite number"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_writer_rejects_unordered_value_added_after_validation(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    trace.events[0].payload["value"] = {"alpha", "beta"}
    output = tmp_path / "traces.jsonl"

    with pytest.raises(ValueError, match="unordered set"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_writer_rejects_non_string_object_key_added_after_validation(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    trace.events[0].payload["nested"] = {1: "integer", "1": "string"}
    output = tmp_path / "traces.jsonl"

    with pytest.raises(ValueError, match="non-string object key"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_writer_rejects_recursive_container_added_after_validation(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    recursive: list[object] = []
    recursive.append(recursive)
    trace.events[0].payload["recursive"] = recursive
    output = tmp_path / "traces.jsonl"

    with pytest.raises(ValueError, match="recursive container"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_writer_rejects_invalid_sequence_added_after_validation(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    trace.events[1].sequence = 7
    output = tmp_path / "traces.jsonl"

    with pytest.raises(ValueError, match="contiguous"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_writer_rejects_naive_timestamp_added_after_validation(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    trace.events[0].timestamp = datetime(2026, 8, 9, 4, 0)
    output = tmp_path / "traces.jsonl"

    with pytest.raises(ValueError, match="timezone-aware"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_writer_rejects_trace_subclass(tmp_path: Path) -> None:
    class TraceSubclass(Trace):
        pass

    trace = TraceSubclass(trace_id="trace", events=[_event(0), _event(1)])
    output = tmp_path / "traces.jsonl"

    with pytest.raises(ValueError, match="expected Trace, got TraceSubclass"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_writer_emits_strict_json(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    trace.events[0].payload["value"] = 1.25
    output = tmp_path / "traces.jsonl"
    write_traces_jsonl(output, [trace])

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[0], parse_constant=_reject_json_constant)
    assert payload["payload"]["value"] == 1.25
