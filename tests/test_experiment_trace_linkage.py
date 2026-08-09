from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from e2h.experiment import ExperimentExecution, ExperimentResult, ExperimentRun, VariantSummary
from e2h.runner import RunResult, RunStatus
from e2h.trace import Trace, trace_from_run_result

START = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)


def _run_result(capsule_id: str = "capsule") -> RunResult:
    return RunResult(
        capsule_id=capsule_id,
        status=RunStatus.PASSED,
        started_at=START + timedelta(seconds=1),
        finished_at=START + timedelta(seconds=2),
        duration_seconds=1.0,
        checks=[],
    )


def _result() -> ExperimentResult:
    run_result = _run_result()
    return ExperimentResult(
        experiment_id="experiment",
        capsule_id="capsule",
        started_at=START,
        finished_at=START + timedelta(seconds=3),
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
                mean_duration_seconds=1.0,
            )
        ],
    )


def _trace(
    *,
    trace_id: str = "trace",
    experiment_id: str = "experiment",
    variant_id: str = "variant",
    repetition: int = 0,
    capsule_id: str = "capsule",
) -> Trace:
    return trace_from_run_result(
        _run_result(capsule_id),
        run_id=trace_id,
        experiment_id=experiment_id,
        variant_id=variant_id,
        repetition=repetition,
    )


def test_experiment_execution_requires_one_trace_per_run() -> None:
    with pytest.raises(ValidationError, match="one-to-one"):
        ExperimentExecution(result=_result(), traces=[])


def test_experiment_execution_requires_matching_trace_id() -> None:
    with pytest.raises(ValidationError, match="trace ids must match"):
        ExperimentExecution(result=_result(), traces=[_trace(trace_id="other")])


@pytest.mark.parametrize(
    ("trace", "message"),
    [
        (_trace(experiment_id="other"), "experiment_id"),
        (_trace(capsule_id="other"), "capsule_id"),
        (_trace(variant_id="other"), "variant_id"),
        (_trace(repetition=1), "repetition"),
    ],
)
def test_experiment_execution_requires_matching_trace_context(
    trace: Trace,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        ExperimentExecution(result=_result(), traces=[trace])


def test_experiment_execution_accepts_linked_trace() -> None:
    execution = ExperimentExecution(result=_result(), traces=[_trace()])

    assert execution.traces[0].trace_id == execution.result.runs[0].trace_id
