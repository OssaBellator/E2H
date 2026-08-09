from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType, write_traces_jsonl


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


def test_writer_emits_strict_json(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    trace.events[0].payload["value"] = 1.25
    output = tmp_path / "traces.jsonl"
    write_traces_jsonl(output, [trace])

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0], parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))[
        "payload"
    ]["value"] == 1.25
