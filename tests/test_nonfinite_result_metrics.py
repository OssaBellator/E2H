from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from e2h.experiment import ExperimentResult, ExperimentRun, VariantSummary
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.store_rows import ArtifactError, normalize_rows
from e2h.trace import trace_from_run_result

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
SOURCE = "0" * 64


def _run(duration: float = 1) -> RunResult:
    return RunResult(
        capsule_id="capsule",
        status=RunStatus.PASSED,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=duration,
        checks=[],
    )


def _experiment(duration: float = 1) -> ExperimentResult:
    run = _run()
    return ExperimentResult(
        experiment_id="matrix",
        capsule_id="capsule",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=duration,
        runs=[
            ExperimentRun(
                run_id="matrix.baseline.000",
                variant_id="baseline",
                repetition=0,
                trace_id="matrix.baseline.000",
                result=run,
            )
        ],
        summaries=[
            VariantSummary(
                variant_id="baseline",
                runs=1,
                passed=1,
                failed=0,
                errors=0,
                pass_rate=1,
                mean_duration_seconds=1,
            )
        ],
    )


def test_command_result_rejects_positive_infinite_duration() -> None:
    with pytest.raises(ValidationError):
        CommandResult(
            id="check",
            argv=["python"],
            cwd=".",
            status=CheckStatus.PASSED,
            duration_seconds=float("inf"),
        )


def test_run_result_rejects_positive_infinite_duration() -> None:
    with pytest.raises(ValidationError):
        _run(float("inf"))


def test_variant_summary_rejects_positive_infinite_mean_duration() -> None:
    with pytest.raises(ValidationError):
        VariantSummary(
            variant_id="baseline",
            runs=1,
            passed=1,
            failed=0,
            errors=0,
            pass_rate=1,
            mean_duration_seconds=float("inf"),
        )


def test_experiment_result_rejects_positive_infinite_duration() -> None:
    with pytest.raises(ValidationError):
        _experiment(float("inf"))


def test_trace_revalidation_rejects_mutated_infinite_run_duration() -> None:
    result = _run()
    result.duration_seconds = float("inf")

    with pytest.raises(ValueError, match="invalid run result"):
        trace_from_run_result(result, run_id="trace")


def test_store_normalization_rejects_mutated_infinite_run_duration() -> None:
    result = _run()
    result.duration_seconds = float("inf")

    with pytest.raises(ArtifactError, match="invalid run artifact"):
        normalize_rows(SOURCE, "run", result)


def test_finite_result_metrics_remain_valid() -> None:
    result = _run(1.25)

    assert result.duration_seconds == 1.25
    assert _experiment(2.5).duration_seconds == 2.5
