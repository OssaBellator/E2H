from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from e2h.experiment import ExperimentResult, ExperimentRun, VariantSummary
from e2h.runner import RunResult, RunStatus
from e2h.store_rows import ArtifactError, normalize_rows

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
SOURCE = "0" * 64


def _run(*, status: RunStatus = RunStatus.PASSED) -> RunResult:
    return RunResult(
        capsule_id="capsule",
        status=status,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=1,
        checks=[],
    )


def _experiment() -> ExperimentResult:
    result = _run()
    return ExperimentResult(
        experiment_id="matrix",
        capsule_id="capsule",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=1,
        runs=[
            ExperimentRun(
                run_id="matrix.baseline.000",
                variant_id="baseline",
                repetition=0,
                trace_id="matrix.baseline.000",
                result=result,
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


@pytest.mark.parametrize("digest", ["0" * 63, "A" * 64, "g" * 64])
def test_normalize_rows_rejects_invalid_source_digest(digest: str) -> None:
    with pytest.raises(ArtifactError, match="lowercase SHA-256"):
        normalize_rows(digest, "run", _run())


def test_normalize_rows_rejects_unknown_kind_instead_of_falling_through() -> None:
    invalid_kind = cast(object, "unknown")
    with pytest.raises(ArtifactError, match="unknown normalized artifact kind"):
        normalize_rows(SOURCE, invalid_kind, _experiment())  # type: ignore[arg-type]


def test_normalize_rows_revalidates_mutated_run_result() -> None:
    result = _run()
    result.status = "broken"  # type: ignore[assignment]

    with pytest.raises(ArtifactError, match="invalid run artifact"):
        normalize_rows(SOURCE, "run", result)


def test_normalize_rows_revalidates_mutated_experiment_result() -> None:
    result = _experiment()
    result.summaries[0].passed = 0

    with pytest.raises(ArtifactError, match="invalid experiment artifact"):
        normalize_rows(SOURCE, "experiment", result)


def test_normalize_rows_preserves_valid_run_shape() -> None:
    runs, checks, summaries, failures = normalize_rows(SOURCE, "run", _run())

    assert len(runs) == 1
    assert runs[0][1] == SOURCE
    assert runs[0][7] == "passed"
    assert checks == []
    assert summaries == []
    assert failures == []
