from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.failures import (
    FailureCode,
    FailureSummary,
    summarize_failures,
    timeout_failure,
    unexpected_exit_failure,
    working_directory_failure,
)


def test_summary_rejects_lower_priority_primary_impact() -> None:
    with pytest.raises(ValidationError, match="highest-priority failure impact"):
        FailureSummary(
            total=2,
            evaluation_failures=1,
            infrastructure_errors=1,
            not_evaluated=0,
            by_category={"environment": 1, "resource": 1},
            by_code={"timeout": 1, "working_directory_missing": 1},
            primary_check_id="timeout",
            primary_code=FailureCode.TIMEOUT,
        )


def test_summary_accepts_highest_priority_primary_impact() -> None:
    summary = FailureSummary(
        total=2,
        evaluation_failures=1,
        infrastructure_errors=1,
        not_evaluated=0,
        by_category={"environment": 1, "resource": 1},
        by_code={"timeout": 1, "working_directory_missing": 1},
        primary_check_id="workspace",
        primary_code=FailureCode.WORKING_DIRECTORY_MISSING,
    )

    assert summary.primary_code is FailureCode.WORKING_DIRECTORY_MISSING


def test_summary_allows_primary_tie_within_same_impact() -> None:
    summary = FailureSummary(
        total=2,
        evaluation_failures=2,
        infrastructure_errors=0,
        not_evaluated=0,
        by_category={"resource": 1, "task": 1},
        by_code={"timeout": 1, "unexpected_exit": 1},
        primary_check_id="timeout",
        primary_code=FailureCode.TIMEOUT,
    )

    assert summary.primary_code is FailureCode.TIMEOUT


def test_generated_summary_selects_infrastructure_before_earlier_evaluation_failure() -> None:
    summary = summarize_failures(
        [
            ("timeout", timeout_failure(1.0, "local")),
            ("workspace", working_directory_failure()),
            ("exit", unexpected_exit_failure(2, [0])),
        ]
    )

    assert summary.primary_check_id == "workspace"
    assert summary.primary_code is FailureCode.WORKING_DIRECTORY_MISSING
