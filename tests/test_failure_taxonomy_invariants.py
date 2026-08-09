from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.failures import (
    FailureCategory,
    FailureCode,
    FailureImpact,
    FailureRecord,
    Retryability,
    launch_failure,
    skipped_failure,
    summarize_failures,
    timeout_failure,
    unexpected_exit_failure,
)


def _failure(**overrides: object) -> FailureRecord:
    values: dict[str, object] = {
        "category": FailureCategory.TASK,
        "code": FailureCode.UNEXPECTED_EXIT,
        "impact": FailureImpact.EVALUATION_FAILURE,
        "retryability": Retryability.NO,
        "summary": "unexpected exit",
    }
    values.update(overrides)
    return FailureRecord.model_validate(values)


def test_failure_record_requires_category_matching_code() -> None:
    with pytest.raises(ValidationError, match="requires category 'resource'"):
        _failure(code=FailureCode.TIMEOUT)


def test_non_skipped_failure_rejects_caused_by_check_id() -> None:
    with pytest.raises(ValidationError, match="only skipped failures"):
        _failure(caused_by_check_id="earlier")


def test_summarize_failures_revalidates_mutated_taxonomy() -> None:
    failure = _failure()
    failure.category = FailureCategory.RESOURCE

    with pytest.raises(ValueError, match="invalid failure record"):
        summarize_failures([("check", failure)])


def test_summarize_failures_revalidates_mutated_causal_link() -> None:
    failure = _failure()
    failure.caused_by_check_id = "earlier"

    with pytest.raises(ValueError, match="invalid failure record"):
        summarize_failures([("check", failure)])


def test_generated_failure_helpers_preserve_valid_taxonomy() -> None:
    generated = [
        unexpected_exit_failure(1, [0]),
        unexpected_exit_failure(-9, [0]),
        timeout_failure(1.0, "local"),
        launch_failure(FileNotFoundError(), "local"),
        skipped_failure("earlier"),
    ]

    summary = summarize_failures(
        [(f"check-{index}", failure) for index, failure in enumerate(generated)]
    )

    assert summary.total == len(generated)
    assert summary.by_category[FailureCategory.TASK.value] == 2
    assert summary.by_category[FailureCategory.RESOURCE.value] == 1
    assert summary.by_category[FailureCategory.DEPENDENCY.value] == 1
    assert summary.by_category[FailureCategory.CONTROL_FLOW.value] == 1
