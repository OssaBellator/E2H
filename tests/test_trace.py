from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.trace import (
    Trace,
    TraceContext,
    TraceEvent,
    TraceEventType,
    trace_from_run_result,
    write_json_atomic,
    write_traces_jsonl,
)


def run_result() -> RunResult:
    started = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
    return RunResult(
        capsule_id="capsule",
        status=RunStatus.PASSED,
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        duration_seconds=1,
        checks=[
            CommandResult(
                id="check",
                argv=["python", "-V"],
                cwd=".",
                status=CheckStatus.PASSED,
                exit_code=0,
                duration_seconds=0.25,
                stdout="Python\n",
            )
        ],
    )


def test_trace_from_run_result_normalizes_observable_events() -> None:
    trace = trace_from_run_result(
        run_result(),
        run_id="experiment.variant.000",
        experiment_id="experiment",
        variant_id="variant",
        repetition=0,
        metadata={"model": "test"},
    )
    assert [event.event_type for event in trace.events] == [
        TraceEventType.RUN_STARTED,
        TraceEventType.CHECK_COMPLETED,
        TraceEventType.RUN_COMPLETED,
    ]
    assert [event.sequence for event in trace.events] == [0, 1, 2]
    assert trace.events[1].payload["stdout"] == "Python\n"
    assert trace.events[1].attributes["timestamp_source"] == "derived_from_check_durations"
    assert trace.events[0].context.metadata == {"model": "test"}


def event(sequence: int, *, trace_id: str = "trace", seconds: int = 0) -> TraceEvent:
    return TraceEvent(
        trace_id=trace_id,
        sequence=sequence,
        event_type=TraceEventType.RUN_STARTED,
        timestamp=datetime(2026, 8, 5, 10, 0, tzinfo=UTC) + timedelta(seconds=seconds),
        context=TraceContext(run_id="trace", capsule_id="capsule"),
    )


def test_trace_rejects_sequence_gap() -> None:
    with pytest.raises(ValidationError, match="contiguous"):
        Trace(trace_id="trace", events=[event(0), event(2)])


def test_trace_rejects_mixed_trace_ids() -> None:
    with pytest.raises(ValidationError, match="trace_id"):
        Trace(trace_id="trace", events=[event(0), event(1, trace_id="other")])


def test_trace_rejects_decreasing_timestamps() -> None:
    with pytest.raises(ValidationError, match="nondecreasing"):
        Trace(trace_id="trace", events=[event(0, seconds=2), event(1, seconds=1)])


def test_trace_event_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        TraceEvent(
            trace_id="trace",
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=datetime(2026, 8, 5, 10, 0),
            context=TraceContext(run_id="trace", capsule_id="capsule"),
        )


def test_trace_event_rejects_non_json_payload() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        TraceEvent(
            trace_id="trace",
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=datetime(2026, 8, 5, 10, 0, tzinfo=UTC),
            context=TraceContext(run_id="trace", capsule_id="capsule"),
            payload={"bad": object()},
        )


def test_write_traces_jsonl_writes_one_event_per_line(tmp_path: Path) -> None:
    trace = trace_from_run_result(run_result(), run_id="trace")
    output = tmp_path / "nested" / "traces.jsonl"
    write_traces_jsonl(output, [trace])
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["sequence"] for line in lines] == [0, 1, 2]


def test_write_traces_jsonl_handles_empty_collection(tmp_path: Path) -> None:
    output = tmp_path / "traces.jsonl"
    write_traces_jsonl(output, [])
    assert output.read_text(encoding="utf-8") == ""


def test_write_json_atomic_replaces_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    output.write_text("old", encoding="utf-8")
    write_json_atomic(output, "new\n")
    assert output.read_text(encoding="utf-8") == "new\n"
    assert not list(tmp_path.glob(".result.json.*"))


def test_write_json_atomic_cleans_temporary_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "result.json"

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError(f"cannot replace {source} with {destination}")

    monkeypatch.setattr("e2h.trace.os.replace", fail_replace)
    with pytest.raises(OSError, match="cannot replace"):
        write_json_atomic(output, "payload")
    assert not output.exists()
    assert not list(tmp_path.glob(".result.json.*"))
