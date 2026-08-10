from __future__ import annotations

import errno

import pytest
from pydantic import ValidationError

from e2h.failures import (
    FailureCategory,
    FailureCode,
    FailureImpact,
    FailureRecord,
    FailureSummary,
    Retryability,
    launch_failure,
    output_capture_failure,
    sandbox_failure,
    skipped_failure,
    summarize_failures,
    timeout_failure,
    unexpected_exit_failure,
    working_directory_failure,
)
from e2h.runner import CheckStatus, CommandResult


def _generated_records() -> list[FailureRecord]:
    return [
        unexpected_exit_failure(2, [0]),
        unexpected_exit_failure(-9, [0]),
        timeout_failure(1.0, "local"),
        launch_failure(FileNotFoundError(errno.ENOENT, "missing"), "local"),
        launch_failure(PermissionError(errno.EACCES, "denied"), "local"),
        launch_failure(OSError(errno.EIO, "io"), "local"),
        working_directory_failure(),
        sandbox_failure(configuration=True),
        sandbox_failure(),
        timeout_failure(1.0, "container", infrastructure_code=FailureCode.SANDBOX_CLEANUP),
        output_capture_failure("local"),
        skipped_failure("failed"),
    ]


def _different_impact(value: FailureImpact) -> FailureImpact:
    return next(candidate for candidate in FailureImpact if candidate is not value)


def _different_retryability(value: Retryability) -> Retryability:
    return next(candidate for candidate in Retryability if candidate is not value)


def test_generated_failure_records_cover_every_code() -> None:
    records = _generated_records()

    assert {record.code for record in records} == set(FailureCode)


@pytest.mark.parametrize("record", _generated_records(), ids=lambda record: record.code.value)
def test_failure_record_rejects_impact_not_owned_by_code(record: FailureRecord) -> None:
    payload = record.model_dump(mode="python")
    payload["impact"] = _different_impact(record.impact)

    with pytest.raises(ValidationError, match="requires impact"):
        FailureRecord.model_validate(payload)


@pytest.mark.parametrize("record", _generated_records(), ids=lambda record: record.code.value)
def test_failure_record_rejects_retryability_not_owned_by_code(record: FailureRecord) -> None:
    payload = record.model_dump(mode="python")
    payload["retryability"] = _different_retryability(record.retryability)

    with pytest.raises(ValidationError, match="requires retryability"):
        FailureRecord.model_validate(payload)


def test_nested_failure_record_is_revalidated() -> None:
    failure = timeout_failure(1.0, "local")
    failure.impact = FailureImpact.INFRASTRUCTURE_ERROR

    with pytest.raises(ValidationError, match="requires impact"):
        CommandResult(
            id="timeout",
            argv=["python", "-V"],
            cwd=".",
            status=CheckStatus.TIMED_OUT,
            duration_seconds=1.0,
            failure=failure,
        )


def test_failure_summary_rejects_impact_counts_not_derived_from_codes() -> None:
    with pytest.raises(ValidationError, match="impact counts must match failure code taxonomy"):
        FailureSummary(
            total=1,
            evaluation_failures=0,
            infrastructure_errors=1,
            not_evaluated=0,
            by_category={FailureCategory.RESOURCE.value: 1},
            by_code={FailureCode.TIMEOUT.value: 1},
            primary_check_id="timeout",
            primary_code=FailureCode.TIMEOUT,
        )


def test_generated_summary_preserves_impact_taxonomy() -> None:
    summary = summarize_failures(
        [
            ("task", unexpected_exit_failure(2, [0])),
            ("infra", working_directory_failure()),
            ("skipped", skipped_failure("task")),
        ]
    )

    assert summary.evaluation_failures == 1
    assert summary.infrastructure_errors == 1
    assert summary.not_evaluated == 1
