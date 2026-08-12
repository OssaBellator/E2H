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


def test_unexpected_exit_and_signal_are_distinct_task_failures() -> None:
    exit_failure = unexpected_exit_failure(2, [0])
    assert exit_failure.category is FailureCategory.TASK
    assert exit_failure.code is FailureCode.UNEXPECTED_EXIT
    assert exit_failure.impact is FailureImpact.EVALUATION_FAILURE
    assert exit_failure.retryability is Retryability.NO
    assert exit_failure.details == {"actual_exit_code": 2, "expected_exit_codes": [0]}

    signal_failure = unexpected_exit_failure(-9, [0])
    assert signal_failure.code is FailureCode.SIGNAL_TERMINATION
    assert signal_failure.details["signal"] == 9
    assert signal_failure.details["actual_exit_code"] == -9


def test_timeout_classification_preserves_typed_infrastructure_complications() -> None:
    plain = timeout_failure(1.5, "local")
    assert plain.category is FailureCategory.RESOURCE
    assert plain.code is FailureCode.TIMEOUT
    assert plain.impact is FailureImpact.EVALUATION_FAILURE
    assert plain.retryability is Retryability.MAYBE

    cleanup = timeout_failure(
        2,
        "container",
        infrastructure_code=FailureCode.SANDBOX_CLEANUP,
    )
    assert cleanup.category is FailureCategory.SANDBOX
    assert cleanup.code is FailureCode.SANDBOX_CLEANUP
    assert cleanup.impact is FailureImpact.INFRASTRUCTURE_ERROR
    assert cleanup.causes == [FailureCode.TIMEOUT]

    capture = timeout_failure(
        3,
        "local",
        infrastructure_code=FailureCode.OUTPUT_CAPTURE,
    )
    assert capture.category is FailureCategory.OBSERVABILITY
    assert capture.code is FailureCode.OUTPUT_CAPTURE
    assert capture.causes == [FailureCode.TIMEOUT]


def test_typed_launch_errors_do_not_depend_on_exception_messages() -> None:
    missing = launch_failure(FileNotFoundError(errno.ENOENT, "anything"), "local")
    denied = launch_failure(PermissionError(errno.EACCES, "anything"), "container")
    generic = launch_failure(OSError(errno.EIO, "anything"), "local")

    assert missing.code is FailureCode.COMMAND_NOT_FOUND
    assert missing.category is FailureCategory.DEPENDENCY
    assert missing.details == {"backend": "local", "errno": errno.ENOENT}
    assert denied.code is FailureCode.PERMISSION_DENIED
    assert denied.details["backend"] == "container"
    assert generic.code is FailureCode.PROCESS_LAUNCH_ERROR
    assert generic.retryability is Retryability.UNKNOWN


def test_environment_sandbox_observability_and_control_flow_helpers() -> None:
    cwd = working_directory_failure()
    assert cwd.code is FailureCode.WORKING_DIRECTORY_MISSING
    assert cwd.impact is FailureImpact.INFRASTRUCTURE_ERROR

    runtime = sandbox_failure()
    configuration = sandbox_failure(configuration=True)
    assert runtime.code is FailureCode.SANDBOX_RUNTIME
    assert configuration.code is FailureCode.SANDBOX_CONFIGURATION

    capture = output_capture_failure("container")
    assert capture.details == {"backend": "container"}
    assert capture.code is FailureCode.OUTPUT_CAPTURE

    skipped = skipped_failure("tests")
    assert skipped.code is FailureCode.SKIPPED_AFTER_FAILURE
    assert skipped.impact is FailureImpact.NOT_EVALUATED
    assert skipped.caused_by_check_id == "tests"


def test_failure_summary_is_deterministic_and_prioritizes_infrastructure() -> None:
    records = [
        ("contract", unexpected_exit_failure(1, [0])),
        ("capture", output_capture_failure("local")),
        ("later-infra", working_directory_failure()),
        ("blocked", skipped_failure("capture")),
    ]
    summary = summarize_failures(records)
    assert summary.total == 4
    assert summary.evaluation_failures == 1
    assert summary.infrastructure_errors == 2
    assert summary.not_evaluated == 1
    assert summary.primary_check_id == "capture"
    assert summary.primary_code is FailureCode.OUTPUT_CAPTURE
    assert summary.by_category == {
        "control_flow": 1,
        "environment": 1,
        "observability": 1,
        "task": 1,
    }
    assert summary.by_code == {
        "output_capture": 1,
        "skipped_after_failure": 1,
        "unexpected_exit": 1,
        "working_directory_missing": 1,
    }


def test_empty_failure_summary_is_valid() -> None:
    summary = summarize_failures([("passed", None)])
    assert summary.total == 0
    assert summary.primary_code is None
    assert summary.by_code == {}


def test_failure_details_must_be_bounded_json_without_raw_output() -> None:
    base = {
        "category": "task",
        "code": "unexpected_exit",
        "impact": "evaluation_failure",
        "retryability": "no",
        "summary": "failed",
    }
    with pytest.raises(ValidationError, match="canonical JSON"):
        FailureRecord(**base, details={"value": object()})
    with pytest.raises(ValidationError, match="canonical JSON"):
        FailureRecord(**base, details={"value": ({"safe": True},)})
    with pytest.raises(ValidationError, match="canonical JSON"):
        FailureRecord(**base, details={"value": {1: "not a JSON object key"}})
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError, match="canonical JSON"):
            FailureRecord(**base, details={"value": value})
    with pytest.raises(ValidationError, match="4096 bytes"):
        FailureRecord(**base, details={"value": "x" * 5000})
    for details in (
        {"stdout": "secret"},
        {"diagnostic": {"stderr": "secret"}},
        {"items": [{"output_text": "secret"}]},
        {"RAW_OUTPUT": "secret"},
    ):
        with pytest.raises(ValidationError, match="raw command output"):
            FailureRecord(**base, details=details)


def test_failure_summary_rejects_inconsistent_primary_and_negative_counts() -> None:
    with pytest.raises(ValidationError, match="must not define a primary"):
        FailureSummary(
            primary_check_id="check",
            primary_code=FailureCode.UNEXPECTED_EXIT,
        )
    with pytest.raises(ValidationError, match="require a primary"):
        FailureSummary(
            total=1,
            evaluation_failures=1,
            by_category={"task": 1},
            by_code={"unexpected_exit": 1},
        )
    with pytest.raises(ValidationError, match="counts must be positive"):
        FailureSummary(
            total=0,
            by_category={"task": -1, "resource": 1},
            by_code={},
        )


def test_failure_causes_and_skipped_links_are_validated() -> None:
    base = {
        "category": "resource",
        "code": "timeout",
        "impact": "evaluation_failure",
        "retryability": "maybe",
        "summary": "timeout",
    }
    with pytest.raises(ValidationError, match="must not repeat"):
        FailureRecord(**base, causes=[FailureCode.TIMEOUT])
    with pytest.raises(ValidationError, match="must be unique"):
        FailureRecord(
            **base,
            causes=[FailureCode.OUTPUT_CAPTURE, FailureCode.OUTPUT_CAPTURE],
        )
    with pytest.raises(ValidationError, match="require caused_by_check_id"):
        FailureRecord(
            category="control_flow",
            code="skipped_after_failure",
            impact="not_evaluated",
            retryability="after_fix",
            summary="skipped",
        )
