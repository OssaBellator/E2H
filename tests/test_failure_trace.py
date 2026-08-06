from __future__ import annotations

from datetime import UTC, datetime, timedelta

from e2h.failures import summarize_failures, timeout_failure
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.trace import TraceEventType, trace_from_run_result


def test_failure_record_and_summary_propagate_to_trace() -> None:
    started = datetime(2026, 8, 6, tzinfo=UTC)
    failure = timeout_failure(2.5, "container")
    check = CommandResult(
        id="slow",
        argv=["python", "-c", "pass"],
        cwd=".",
        status=CheckStatus.TIMED_OUT,
        exit_code=None,
        duration_seconds=2.5,
        stdout="",
        stderr="",
        failure=failure,
    )
    result = RunResult(
        capsule_id="trace-failure",
        status=RunStatus.FAILED,
        started_at=started,
        finished_at=started + timedelta(seconds=2.5),
        duration_seconds=2.5,
        checks=[check],
        failure_summary=summarize_failures([("slow", failure)]),
    )
    trace = trace_from_run_result(result, run_id="failure-run")
    check_event = next(
        event for event in trace.events if event.event_type is TraceEventType.CHECK_COMPLETED
    )
    assert check_event.payload["failure"]["code"] == "timeout"
    completed = trace.events[-1]
    assert completed.event_type is TraceEventType.RUN_COMPLETED
    assert completed.payload["failure_summary"]["primary_code"] == "timeout"
    assert completed.payload["failure_summary"]["evaluation_failures"] == 1
