"""Regression coverage for optimizer feedback run-result revalidation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from e2h.optimizer_adapters import OptimizerAdapterError, feedback_from_run_result
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus

NOW = datetime(2026, 8, 10, 7, 45, tzinfo=UTC)

# Mutate valid runner artifacts after construction to exercise the adapter trust boundary.


def _check() -> CommandResult:
    return CommandResult(
        id="check",
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.PASSED,
        exit_code=0,
        duration_seconds=0.1,
    )


def _result(*, checks: list[CommandResult] | None = None) -> RunResult:
    return RunResult(
        capsule_id="capsule",
        status=RunStatus.PASSED,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=1.0,
        checks=[] if checks is None else checks,
    )


def test_optimizer_feedback_revalidates_mutated_run_status() -> None:
    result = _result()
    result.status = "broken"  # type: ignore[assignment]

    with pytest.raises(OptimizerAdapterError, match="invalid run result"):
        feedback_from_run_result(result)


def test_optimizer_feedback_revalidates_mutated_nested_check() -> None:
    check = _check()
    result = _result(checks=[check])
    result.checks[0].duration_seconds = -1

    with pytest.raises(OptimizerAdapterError, match="invalid run result"):
        feedback_from_run_result(result)


def test_optimizer_feedback_preserves_valid_run_result() -> None:
    feedback = feedback_from_run_result(_result(checks=[_check()]))

    assert feedback.capsule_id == "capsule"
    assert feedback.run_status == "passed"
    assert feedback.score == 1.0
    assert feedback.checks[0].status == "passed"
