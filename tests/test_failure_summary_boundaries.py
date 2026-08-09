from __future__ import annotations

import pytest

from e2h.failures import (
    FailureCategory,
    FailureCode,
    FailureImpact,
    Retryability,
    summarize_failures,
    unexpected_exit_failure,
    working_directory_failure,
)


def test_failure_summary_revalidates_mutated_category() -> None:
    failure = unexpected_exit_failure(2, [0])
    failure.category = "broken"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="invalid failure record"):
        summarize_failures([("check", failure)])


def test_failure_summary_revalidates_mutated_causes() -> None:
    failure = unexpected_exit_failure(2, [0])
    failure.causes.append(FailureCode.UNEXPECTED_EXIT)

    with pytest.raises(ValueError, match="invalid failure record"):
        summarize_failures([("check", failure)])


def test_failure_summary_preserves_existing_ranking() -> None:
    summary = summarize_failures(
        [
            ("task", unexpected_exit_failure(2, [0])),
            ("infra", working_directory_failure()),
        ]
    )

    assert summary.total == 2
    assert summary.evaluation_failures == 1
    assert summary.infrastructure_errors == 1
    assert summary.primary_check_id == "infra"
    assert summary.primary_code is FailureCode.WORKING_DIRECTORY_MISSING
    assert summary.by_category == {
        FailureCategory.ENVIRONMENT.value: 1,
        FailureCategory.TASK.value: 1,
    }
    assert summary.by_code == {
        FailureCode.UNEXPECTED_EXIT.value: 1,
        FailureCode.WORKING_DIRECTORY_MISSING.value: 1,
    }


def test_failure_summary_still_accepts_empty_input() -> None:
    summary = summarize_failures([])

    assert summary.total == 0
    assert summary.primary_code is None
    assert summary.evaluation_failures == 0
    assert summary.infrastructure_errors == 0
    assert summary.not_evaluated == 0


def test_failure_enum_values_remain_unchanged() -> None:
    assert FailureImpact.EVALUATION_FAILURE.value == "evaluation_failure"
    assert Retryability.NO.value == "no"
