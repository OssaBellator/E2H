from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.failures import (
    FailureCode,
    FailureSummary,
    summarize_failures,
    timeout_failure,
    unexpected_exit_failure,
)


def _summary(**overrides: object) -> FailureSummary:
    values: dict[str, object] = {
        "total": 1,
        "evaluation_failures": 1,
        "infrastructure_errors": 0,
        "not_evaluated": 0,
        "by_category": {"task": 1},
        "by_code": {"unexpected_exit": 1},
        "primary_check_id": "check",
        "primary_code": FailureCode.UNEXPECTED_EXIT,
    }
    values.update(overrides)
    return FailureSummary.model_validate(values)


def test_failure_summary_rejects_unknown_code_key() -> None:
    with pytest.raises(ValidationError, match="known failure codes"):
        _summary(by_code={"unknown": 1})


def test_failure_summary_rejects_category_not_derived_from_codes() -> None:
    with pytest.raises(ValidationError, match="match failure code taxonomy"):
        _summary(
            by_code={FailureCode.TIMEOUT.value: 1},
            by_category={"task": 1},
            primary_code=FailureCode.TIMEOUT,
        )


def test_failure_summary_rejects_zero_count_entries() -> None:
    with pytest.raises(ValidationError, match="code counts must be positive"):
        _summary(
            total=1,
            by_code={FailureCode.UNEXPECTED_EXIT.value: 1, FailureCode.TIMEOUT.value: 0},
        )


def test_failure_summary_requires_primary_code_in_counts() -> None:
    with pytest.raises(ValidationError, match="primary failure code must appear"):
        _summary(primary_code=FailureCode.TIMEOUT)


def test_empty_failure_summary_remains_valid() -> None:
    summary = FailureSummary()

    assert summary.total == 0
    assert summary.by_category == {}
    assert summary.by_code == {}


def test_generated_summary_preserves_taxonomy_aggregation() -> None:
    summary = summarize_failures(
        [
            ("exit", unexpected_exit_failure(1, [0])),
            ("timeout", timeout_failure(1.0, "local")),
        ]
    )

    assert summary.total == 2
    assert summary.by_category == {"resource": 1, "task": 1}
    assert summary.by_code == {"timeout": 1, "unexpected_exit": 1}
