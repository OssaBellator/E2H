from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from e2h.experiment import ExperimentResult, ExperimentRun, VariantSummary
from e2h.runner import RunResult, RunStatus

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _run(*, capsule_id: str = "capsule", status: RunStatus = RunStatus.PASSED) -> ExperimentRun:
    return ExperimentRun(
        run_id="matrix.baseline.000",
        variant_id="baseline",
        repetition=0,
        trace_id="matrix.baseline.000",
        result=RunResult(
            capsule_id=capsule_id,
            status=status,
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=2),
            duration_seconds=2,
            checks=[],
        ),
    )


def _summary(**overrides: object) -> VariantSummary:
    payload: dict[str, object] = {
        "variant_id": "baseline",
        "runs": 1,
        "passed": 1,
        "failed": 0,
        "errors": 0,
        "pass_rate": 1,
        "mean_duration_seconds": 2,
    }
    payload.update(overrides)
    return VariantSummary.model_validate(payload)


def _result(run: ExperimentRun, summary: VariantSummary) -> ExperimentResult:
    return ExperimentResult(
        experiment_id="matrix",
        capsule_id="capsule",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
        duration_seconds=2,
        runs=[run],
        summaries=[summary],
    )


def test_variant_summary_rejects_impossible_status_counts() -> None:
    with pytest.raises(ValidationError, match="status counts must sum to runs"):
        _summary(passed=2)


def test_variant_summary_rejects_inconsistent_pass_rate() -> None:
    with pytest.raises(ValidationError, match="pass_rate"):
        _summary(pass_rate=0.5)


def test_experiment_result_requires_exact_summary_variant_coverage() -> None:
    summary = _summary(variant_id="other")
    with pytest.raises(ValidationError, match="cover exactly the run variants"):
        _result(_run(), summary)


def test_experiment_result_rejects_summary_counts_that_disagree_with_runs() -> None:
    summary = _summary(passed=0, failed=1, pass_rate=0)
    with pytest.raises(ValidationError, match="counts must match variant runs"):
        _result(_run(), summary)


def test_experiment_result_rejects_summary_mean_that_disagrees_with_runs() -> None:
    with pytest.raises(ValidationError, match="mean duration"):
        _result(_run(), _summary(mean_duration_seconds=1))


def test_experiment_result_requires_nested_runs_to_match_capsule() -> None:
    with pytest.raises(ValidationError, match="declared capsule_id"):
        _result(_run(capsule_id="other"), _summary())


def test_experiment_result_rejects_reversed_wall_clock_interval() -> None:
    with pytest.raises(ValidationError, match="finished_at"):
        ExperimentResult(
            experiment_id="matrix",
            capsule_id="capsule",
            started_at=NOW + timedelta(seconds=2),
            finished_at=NOW,
            duration_seconds=2,
            runs=[_run()],
            summaries=[_summary()],
        )


def test_experiment_result_accepts_consistent_metrics() -> None:
    result = _result(_run(), _summary())

    assert result.summaries[0].pass_rate == 1
    assert result.summaries[0].mean_duration_seconds == 2
