from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from e2h.failures import (
    FailureSummary,
    summarize_failures,
    unexpected_exit_failure,
    working_directory_failure,
)
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus

NOW = datetime(2026, 8, 9, 14, 30, tzinfo=UTC)


def _passed(identifier: str = "check") -> CommandResult:
    return CommandResult(
        id=identifier,
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.PASSED,
        exit_code=0,
        duration_seconds=0.1,
    )


def _failed(identifier: str = "check") -> CommandResult:
    return CommandResult(
        id=identifier,
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.FAILED,
        exit_code=2,
        duration_seconds=0.1,
        failure=unexpected_exit_failure(2, [0]),
    )


def _error(identifier: str = "check") -> CommandResult:
    return CommandResult(
        id=identifier,
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.ERROR,
        duration_seconds=0,
        error="working directory missing",
        failure=working_directory_failure(),
    )


def _run(status: RunStatus, checks: list[CommandResult]) -> RunResult:
    return RunResult(
        capsule_id="capsule",
        status=status,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=1,
        checks=checks,
        failure_summary=summarize_failures((check.id, check.failure) for check in checks),
    )


def test_command_result_requires_failure_for_non_passed_status() -> None:
    with pytest.raises(ValidationError, match="require a failure"):
        CommandResult(
            id="check",
            argv=["python"],
            cwd=".",
            status=CheckStatus.FAILED,
            exit_code=2,
            duration_seconds=0.1,
        )


def test_command_result_rejects_failure_on_passed_status() -> None:
    with pytest.raises(ValidationError, match="must not define a failure"):
        CommandResult(
            id="check",
            argv=["python"],
            cwd=".",
            status=CheckStatus.PASSED,
            exit_code=0,
            duration_seconds=0.1,
            failure=unexpected_exit_failure(2, [0]),
        )


def test_run_result_rejects_reversed_or_naive_intervals() -> None:
    payload = _run(RunStatus.PASSED, [_passed()]).model_dump(mode="python")
    payload["started_at"] = NOW + timedelta(seconds=2)
    with pytest.raises(ValidationError, match="must not precede"):
        RunResult.model_validate(payload)

    payload = _run(RunStatus.PASSED, [_passed()]).model_dump(mode="python")
    payload["started_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError, match="timezone-aware"):
        RunResult.model_validate(payload)


def test_run_result_rejects_duplicate_check_ids() -> None:
    with pytest.raises(ValidationError, match="check ids must be unique"):
        _run(RunStatus.PASSED, [_passed(), _passed()])


@pytest.mark.parametrize(
    ("status", "checks"),
    [
        (RunStatus.FAILED, [_passed()]),
        (RunStatus.PASSED, [_failed()]),
        (RunStatus.FAILED, [_error()]),
    ],
)
def test_run_result_status_must_match_check_outcomes(
    status: RunStatus,
    checks: list[CommandResult],
) -> None:
    with pytest.raises(ValidationError, match="status must match"):
        _run(status, checks)


def test_run_result_failure_summary_must_match_checks() -> None:
    with pytest.raises(ValidationError, match="failure_summary must match"):
        RunResult(
            capsule_id="capsule",
            status=RunStatus.FAILED,
            started_at=NOW,
            finished_at=NOW + timedelta(seconds=1),
            duration_seconds=1,
            checks=[_failed()],
            failure_summary=FailureSummary(),
        )


def test_run_result_accepts_consistent_passed_failed_and_error_shapes() -> None:
    assert _run(RunStatus.PASSED, [_passed()]).status is RunStatus.PASSED
    assert _run(RunStatus.FAILED, [_failed()]).status is RunStatus.FAILED
    assert _run(RunStatus.ERROR, [_error()]).status is RunStatus.ERROR
