from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.trace import TraceEventType, trace_from_run_result


def _run_result() -> RunResult:
    started = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    return RunResult(
        capsule_id="trace-boundary",
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
            )
        ],
    )


def test_trace_conversion_revalidates_mutated_run_status() -> None:
    result = _run_result()
    result.status = "broken"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="invalid run result"):
        trace_from_run_result(result, run_id="trace")


def test_trace_conversion_revalidates_mutated_check_status() -> None:
    result = _run_result()
    result.checks[0].status = "broken"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="invalid run result"):
        trace_from_run_result(result, run_id="trace")


def test_trace_conversion_revalidates_negative_duration() -> None:
    result = _run_result()
    result.duration_seconds = -1

    with pytest.raises(ValueError, match="invalid run result"):
        trace_from_run_result(result, run_id="trace")


def test_trace_conversion_preserves_valid_result() -> None:
    trace = trace_from_run_result(_run_result(), run_id="trace")

    assert [event.event_type for event in trace.events] == [
        TraceEventType.RUN_STARTED,
        TraceEventType.CHECK_COMPLETED,
        TraceEventType.RUN_COMPLETED,
    ]
