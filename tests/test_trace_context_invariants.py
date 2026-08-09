from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.runner import RunResult, RunStatus
from e2h.trace import (
    Trace,
    TraceContext,
    TraceEvent,
    TraceEventType,
    trace_from_run_result,
    write_traces_jsonl,
)

NOW = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)


def _context(**overrides: object) -> TraceContext:
    values: dict[str, object] = {
        "run_id": "trace",
        "capsule_id": "capsule",
        "experiment_id": "experiment",
        "variant_id": "variant",
        "repetition": 0,
    }
    values.update(overrides)
    return TraceContext.model_validate(values)


def _event(sequence: int, context: TraceContext) -> TraceEvent:
    return TraceEvent(
        trace_id="trace",
        sequence=sequence,
        event_type=(
            TraceEventType.RUN_STARTED if sequence == 0 else TraceEventType.RUN_COMPLETED
        ),
        timestamp=NOW + timedelta(seconds=sequence),
        context=context,
    )


def _trace(second_context: TraceContext | None = None) -> Trace:
    return Trace(
        trace_id="trace",
        events=[_event(0, _context()), _event(1, second_context or _context())],
    )


def test_trace_context_run_id_must_match_trace_id() -> None:
    with pytest.raises(ValidationError, match="trace trace_id as run_id"):
        _trace(_context(run_id="other"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"capsule_id": "other"},
        {"experiment_id": "other"},
        {"variant_id": "other"},
        {"repetition": 1},
    ],
)
def test_trace_context_identity_must_remain_stable(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="stable execution identity"):
        _trace(_context(**overrides))


def test_trace_writer_revalidates_mutated_context_before_output(tmp_path: Path) -> None:
    trace = _trace()
    trace.events[1].context.capsule_id = "other"
    output = tmp_path / "trace.jsonl"

    with pytest.raises(ValueError, match="invalid trace"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_trace_from_run_result_preserves_stable_context_identity() -> None:
    result = RunResult(
        capsule_id="capsule",
        status=RunStatus.PASSED,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=1.0,
        checks=[],
    )

    trace = trace_from_run_result(
        result,
        run_id="trace",
        experiment_id="experiment",
        variant_id="variant",
        repetition=0,
    )

    assert all(event.context.run_id == trace.trace_id for event in trace.events)
    assert all(event.context.capsule_id == "capsule" for event in trace.events)
