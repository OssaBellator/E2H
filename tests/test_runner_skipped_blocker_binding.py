from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from e2h.failures import skipped_failure, summarize_failures, unexpected_exit_failure
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus

NOW = datetime(2026, 8, 10, 20, 45, tzinfo=UTC)


def _failed(identifier: str) -> CommandResult:
    return CommandResult(
        id=identifier,
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.FAILED,
        exit_code=2,
        duration_seconds=0.1,
        failure=unexpected_exit_failure(2, [0]),
    )


def _passed(identifier: str) -> CommandResult:
    return CommandResult(
        id=identifier,
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.PASSED,
        exit_code=0,
        duration_seconds=0.1,
    )


def _skipped(identifier: str, blocker: str) -> CommandResult:
    return CommandResult(
        id=identifier,
        argv=["python", "-V"],
        cwd=".",
        status=CheckStatus.SKIPPED,
        duration_seconds=0,
        failure=skipped_failure(blocker),
    )


def _run(checks: list[CommandResult]) -> RunResult:
    return RunResult(
        capsule_id="capsule",
        status=RunStatus.FAILED,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=1,
        checks=checks,
        failure_summary=summarize_failures((check.id, check.failure) for check in checks),
    )


def test_skipped_suffix_must_start_immediately_after_blocker() -> None:
    checks = [_failed("earlier"), _passed("continued"), _skipped("skipped", "earlier")]

    with pytest.raises(ValidationError, match="immediately follow the halting failed check"):
        _run(checks)


def test_skipped_suffix_must_reference_last_executed_failure() -> None:
    checks = [_failed("first"), _failed("blocker"), _skipped("skipped", "first")]

    with pytest.raises(ValidationError, match="reference the check that halted execution"):
        _run(checks)


def test_all_skipped_checks_reference_one_halting_check() -> None:
    checks = [
        _failed("first"),
        _failed("blocker"),
        _skipped("first-skipped", "blocker"),
        _skipped("second-skipped", "first"),
    ]

    with pytest.raises(ValidationError, match="reference the check that halted execution"):
        _run(checks)


def test_valid_skipped_suffix_uses_immediate_blocker() -> None:
    checks = [
        _failed("blocker"),
        _skipped("first-skipped", "blocker"),
        _skipped("second-skipped", "blocker"),
    ]

    result = _run(checks)

    assert result.checks[1].failure is not None
    assert result.checks[1].failure.caused_by_check_id == "blocker"
    assert result.checks[2].failure is not None
    assert result.checks[2].failure.caused_by_check_id == "blocker"
