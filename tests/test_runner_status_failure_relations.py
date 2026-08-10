from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from e2h.failures import (
    output_capture_failure,
    skipped_failure,
    summarize_failures,
    timeout_failure,
    unexpected_exit_failure,
    working_directory_failure,
)
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus

NOW = datetime(2026, 8, 10, 9, 15, tzinfo=UTC)


def _command(
    identifier: str,
    status: CheckStatus,
    failure: object | None,
) -> CommandResult:
    return CommandResult(
        id=identifier,
        argv=["python", "-V"],
        cwd=".",
        status=status,
        exit_code=2 if status is CheckStatus.FAILED else None,
        duration_seconds=0.1,
        failure=failure,
    )


def _run(checks: list[CommandResult]) -> RunResult:
    has_error = any(
        check.failure is not None and check.failure.impact.value == "infrastructure_error"
        for check in checks
    )
    status = RunStatus.ERROR if has_error else RunStatus.FAILED
    return RunResult(
        capsule_id="capsule",
        status=status,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=1,
        checks=checks,
        failure_summary=summarize_failures((check.id, check.failure) for check in checks),
    )


@pytest.mark.parametrize(
    ("status", "failure"),
    [
        (CheckStatus.FAILED, timeout_failure(1, "local")),
        (CheckStatus.TIMED_OUT, unexpected_exit_failure(2, [0])),
        (CheckStatus.ERROR, unexpected_exit_failure(2, [0])),
        (CheckStatus.SKIPPED, working_directory_failure()),
    ],
)
def test_command_result_rejects_failure_code_incompatible_with_status(
    status: CheckStatus,
    failure: object,
) -> None:
    with pytest.raises(ValidationError, match="incompatible failure code"):
        _command("check", status, failure)


def test_command_result_accepts_generated_status_failure_pairs() -> None:
    assert _command(
        "failed",
        CheckStatus.FAILED,
        unexpected_exit_failure(2, [0]),
    ).status is CheckStatus.FAILED
    assert _command(
        "timed-out",
        CheckStatus.TIMED_OUT,
        timeout_failure(1, "local"),
    ).status is CheckStatus.TIMED_OUT
    assert _command(
        "timed-out-capture",
        CheckStatus.TIMED_OUT,
        timeout_failure(1, "local", infrastructure_code=output_capture_failure("local").code),
    ).status is CheckStatus.TIMED_OUT
    assert _command(
        "error",
        CheckStatus.ERROR,
        working_directory_failure(),
    ).status is CheckStatus.ERROR
    assert _command(
        "skipped",
        CheckStatus.SKIPPED,
        skipped_failure("failed"),
    ).status is CheckStatus.SKIPPED


def test_run_result_rejects_skipped_check_without_earlier_blocker() -> None:
    skipped = _command("skipped", CheckStatus.SKIPPED, skipped_failure("missing"))

    with pytest.raises(ValidationError, match="earlier failed check"):
        _run([skipped])


def test_run_result_rejects_skipped_check_pointing_to_passed_check() -> None:
    passed = CommandResult(
        id="passed",
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.PASSED,
        exit_code=0,
        duration_seconds=0.1,
    )
    skipped = _command("skipped", CheckStatus.SKIPPED, skipped_failure("passed"))

    with pytest.raises(ValidationError, match="earlier failed check"):
        _run([passed, skipped])


def test_run_result_rejects_execution_after_skipped_check() -> None:
    failed = _command("failed", CheckStatus.FAILED, unexpected_exit_failure(2, [0]))
    skipped = _command("skipped", CheckStatus.SKIPPED, skipped_failure("failed"))
    passed = CommandResult(
        id="passed",
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.PASSED,
        exit_code=0,
        duration_seconds=0.1,
    )

    with pytest.raises(ValidationError, match="must not follow skipped checks"):
        _run([failed, skipped, passed])


def test_run_result_accepts_skipped_suffix_with_earlier_blocker() -> None:
    failed = _command("failed", CheckStatus.FAILED, unexpected_exit_failure(2, [0]))
    first = _command("first", CheckStatus.SKIPPED, skipped_failure("failed"))
    second = _command("second", CheckStatus.SKIPPED, skipped_failure("failed"))

    result = _run([failed, first, second])

    assert result.status is RunStatus.FAILED
