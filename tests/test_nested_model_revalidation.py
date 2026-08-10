from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from e2h.experiment import (
    ExperimentExecution,
    ExperimentResult,
    ExperimentRun,
    VariantSummary,
)
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType, trace_from_run_result

START = datetime(2026, 8, 10, 2, 0, tzinfo=UTC)
RUN_START = START + timedelta(seconds=1)
RUN_END = START + timedelta(seconds=2)
EXPERIMENT_END = START + timedelta(seconds=3)


def _passed_check() -> CommandResult:
    return CommandResult(
        id="check",
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.PASSED,
        exit_code=0,
        duration_seconds=0.1,
    )


def _run_result(*, checks: list[CommandResult] | None = None) -> RunResult:
    return RunResult(
        capsule_id="capsule",
        status=RunStatus.PASSED,
        started_at=RUN_START,
        finished_at=RUN_END,
        duration_seconds=1.0,
        checks=[] if checks is None else checks,
    )


def _experiment_result(result: RunResult | None = None) -> ExperimentResult:
    run_result = result or _run_result()
    return ExperimentResult(
        experiment_id="experiment",
        capsule_id="capsule",
        started_at=START,
        finished_at=EXPERIMENT_END,
        duration_seconds=3.0,
        runs=[
            ExperimentRun(
                run_id="run",
                variant_id="variant",
                repetition=0,
                trace_id="trace",
                result=run_result,
            )
        ],
        summaries=[
            VariantSummary(
                variant_id="variant",
                runs=1,
                passed=1,
                failed=0,
                errors=0,
                pass_rate=1.0,
                mean_duration_seconds=run_result.duration_seconds,
            )
        ],
    )


def _trace(result: RunResult | None = None) -> Trace:
    return trace_from_run_result(
        result or _run_result(),
        run_id="trace",
        experiment_id="experiment",
        variant_id="variant",
        repetition=0,
    )


def test_run_result_revalidates_mutated_command_field_constraints() -> None:
    check = _passed_check()
    check.duration_seconds = -1

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _run_result(checks=[check])


def test_experiment_run_revalidates_mutated_run_result_constraints() -> None:
    result = _run_result()
    result.duration_seconds = -1

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ExperimentRun(
            run_id="run",
            variant_id="variant",
            repetition=0,
            trace_id="trace",
            result=result,
        )


def test_trace_event_revalidates_mutated_context_constraints() -> None:
    context = TraceContext(
        run_id="trace",
        capsule_id="capsule",
        repetition=0,
    )
    context.repetition = -1

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        TraceEvent(
            trace_id="trace",
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=RUN_START,
            context=context,
        )


def test_trace_revalidates_mutated_event_field_types() -> None:
    trace = _trace()
    first = trace.events[0]
    first.payload = []  # type: ignore[assignment]

    with pytest.raises(ValidationError, match="dictionary"):
        Trace(trace_id="trace", events=[first, trace.events[-1]])


def test_experiment_execution_rejects_mutated_empty_trace_as_validation_error() -> None:
    trace = _trace()
    trace.events = []

    with pytest.raises(ValidationError, match="at least 2 items"):
        ExperimentExecution(result=_experiment_result(), traces=[trace])


def test_experiment_execution_revalidates_mutated_result_constraints() -> None:
    result = _experiment_result()
    result.duration_seconds = -1

    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ExperimentExecution(result=result, traces=[_trace()])
