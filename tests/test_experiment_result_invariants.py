from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from e2h.experiment import ExperimentResult, ExperimentRun, VariantSummary
from e2h.runner import RunResult, RunStatus

START = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
END = START + timedelta(seconds=10)


def _run_result(
    *,
    started_at: datetime = START + timedelta(seconds=1),
    finished_at: datetime = START + timedelta(seconds=2),
) -> RunResult:
    return RunResult(
        capsule_id="capsule",
        status=RunStatus.PASSED,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=1.0,
        checks=[],
    )


def _run(
    run_id: str,
    *,
    trace_id: str | None = None,
    repetition: int = 0,
    result: RunResult | None = None,
) -> ExperimentRun:
    return ExperimentRun(
        run_id=run_id,
        variant_id="variant",
        repetition=repetition,
        trace_id=trace_id or f"trace-{run_id}",
        result=result or _run_result(),
    )


def _summary(runs: int) -> VariantSummary:
    return VariantSummary(
        variant_id="variant",
        runs=runs,
        passed=runs,
        failed=0,
        errors=0,
        pass_rate=1.0,
        mean_duration_seconds=1.0,
    )


def _result(
    runs: list[ExperimentRun],
    *,
    started_at: datetime = START,
    finished_at: datetime = END,
) -> ExperimentResult:
    return ExperimentResult(
        experiment_id="experiment",
        capsule_id="capsule",
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=10.0,
        runs=runs,
        summaries=[_summary(len(runs))],
    )


@pytest.mark.parametrize("field", ["started_at", "finished_at"])
def test_experiment_result_requires_timezone_aware_interval(field: str) -> None:
    values = {"started_at": START, "finished_at": END}
    values[field] = values[field].replace(tzinfo=None)

    with pytest.raises(ValidationError, match="must be timezone-aware"):
        _result([_run("run")], **values)


def test_experiment_result_requires_unique_run_ids() -> None:
    runs = [_run("same", repetition=0), _run("same", trace_id="other", repetition=1)]

    with pytest.raises(ValidationError, match="run ids must be unique"):
        _result(runs)


def test_experiment_result_requires_unique_trace_ids() -> None:
    runs = [_run("one", trace_id="same", repetition=0), _run("two", trace_id="same", repetition=1)]

    with pytest.raises(ValidationError, match="trace ids must be unique"):
        _result(runs)


def test_experiment_result_requires_unique_matrix_cells() -> None:
    runs = [_run("one", repetition=0), _run("two", repetition=0)]

    with pytest.raises(ValidationError, match="variant/repetition cells must be unique"):
        _result(runs)


def test_experiment_result_brackets_run_intervals() -> None:
    outside = _run_result(
        started_at=START - timedelta(seconds=1),
        finished_at=START + timedelta(seconds=1),
    )

    with pytest.raises(ValidationError, match="run intervals must fall within"):
        _result([_run("run", result=outside)])


def test_experiment_result_accepts_distinct_bracketed_matrix_cells() -> None:
    runs = [_run("one", repetition=0), _run("two", repetition=1)]

    result = _result(runs)

    assert [run.run_id for run in result.runs] == ["one", "two"]
