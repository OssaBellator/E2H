from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from e2h.failures import (
    FailureCategory,
    FailureCode,
    FailureImpact,
    FailureRecord,
    Retryability,
    summarize_failures,
)


class CoercibleInt(int):
    pass


def _failure(details: dict[str, Any] | None = None) -> FailureRecord:
    return FailureRecord(
        category=FailureCategory.TASK,
        code=FailureCode.UNEXPECTED_EXIT,
        impact=FailureImpact.EVALUATION_FAILURE,
        retryability=Retryability.NO,
        summary="command returned an unexpected exit code",
        details={} if details is None else details,
    )


def test_failure_details_reject_recursive_values_without_recursion_error() -> None:
    details: dict[str, Any] = {}
    details["self"] = details

    with pytest.raises(ValidationError, match="canonical JSON values"):
        _failure(details)


def test_failure_details_reject_coercible_scalar_subclasses() -> None:
    with pytest.raises(ValidationError, match="canonical JSON values"):
        _failure({"value": CoercibleInt(3)})


def test_failure_summary_revalidates_mutated_coercible_details() -> None:
    failure = _failure()
    failure.details["value"] = CoercibleInt(3)

    with pytest.raises(ValueError, match="invalid failure record"):
        summarize_failures([("check", failure)])


def test_failure_details_preserve_exact_nested_json() -> None:
    details = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}

    assert _failure(details).details == details
