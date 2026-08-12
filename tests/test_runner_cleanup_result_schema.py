from __future__ import annotations

from e2h.failures import (
    FailureCategory,
    FailureCode,
    FailureImpact,
    FailureRecord,
    Retryability,
)
from e2h.runner import CheckStatus, CommandResult


def test_error_result_accepts_completed_sandbox_cleanup_failure() -> None:
    result = CommandResult(
        id="cleanup",
        argv=["check"],
        cwd=".",
        status=CheckStatus.ERROR,
        duration_seconds=0,
        error="completed container cleanup failed",
        failure=FailureRecord(
            category=FailureCategory.SANDBOX,
            code=FailureCode.SANDBOX_CLEANUP,
            impact=FailureImpact.INFRASTRUCTURE_ERROR,
            retryability=Retryability.AFTER_FIX,
            summary="completed container could not be cleaned up safely",
        ),
    )

    assert result.failure is not None
    assert result.failure.code is FailureCode.SANDBOX_CLEANUP
