"""Typed, observable failure taxonomy for deterministic replay outcomes."""

from __future__ import annotations

import errno
import json
from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_DETAILS_BYTES = 4096
_FORBIDDEN_DETAIL_KEYS = frozenset({"stdout", "stderr", "output_text", "raw_output"})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FailureCategory(StrEnum):
    """Stable high-level ownership domain for an observable failure."""

    TASK = "task"
    RESOURCE = "resource"
    DEPENDENCY = "dependency"
    ENVIRONMENT = "environment"
    SANDBOX = "sandbox"
    OBSERVABILITY = "observability"
    CONTROL_FLOW = "control_flow"


class FailureCode(StrEnum):
    """Stable machine-readable replay failure codes."""

    UNEXPECTED_EXIT = "unexpected_exit"
    SIGNAL_TERMINATION = "signal_termination"
    TIMEOUT = "timeout"
    COMMAND_NOT_FOUND = "command_not_found"
    PERMISSION_DENIED = "permission_denied"
    PROCESS_LAUNCH_ERROR = "process_launch_error"
    WORKING_DIRECTORY_MISSING = "working_directory_missing"
    SANDBOX_CONFIGURATION = "sandbox_configuration"
    SANDBOX_RUNTIME = "sandbox_runtime"
    SANDBOX_CLEANUP = "sandbox_cleanup"
    OUTPUT_CAPTURE = "output_capture"
    SKIPPED_AFTER_FAILURE = "skipped_after_failure"


class FailureImpact(StrEnum):
    """How the failure affects evaluation interpretation."""

    EVALUATION_FAILURE = "evaluation_failure"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    NOT_EVALUATED = "not_evaluated"


class Retryability(StrEnum):
    """Whether repeating the same run could reasonably change the outcome."""

    NO = "no"
    MAYBE = "maybe"
    AFTER_FIX = "after_fix"
    UNKNOWN = "unknown"


def _contains_forbidden_detail_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_DETAIL_KEYS:
                return True
            if _contains_forbidden_detail_key(item):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_detail_key(item) for item in value)
    return False


class FailureRecord(StrictModel):
    """One bounded classification derived only from observable execution state."""

    schema_version: Literal["0.1"] = "0.1"
    category: FailureCategory
    code: FailureCode
    impact: FailureImpact
    retryability: Retryability
    summary: str = Field(min_length=1, max_length=255)
    details: dict[str, Any] = Field(default_factory=dict)
    caused_by_check_id: str | None = Field(default=None, max_length=255)
    causes: list[FailureCode] = Field(default_factory=list, max_length=10)

    @field_validator("details")
    @classmethod
    def details_must_be_bounded_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _contains_forbidden_detail_key(value):
            raise ValueError("failure details must not contain raw command output")
        try:
            rendered = json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("failure details must contain canonical JSON values") from exc
        if len(rendered.encode("utf-8")) > _MAX_DETAILS_BYTES:
            raise ValueError(f"failure details exceed {_MAX_DETAILS_BYTES} bytes")
        return value

    @model_validator(mode="after")
    def causes_must_be_unique_and_not_repeat_primary(self) -> FailureRecord:
        if self.code in self.causes:
            raise ValueError("failure causes must not repeat the primary code")
        if len(set(self.causes)) != len(self.causes):
            raise ValueError("failure causes must be unique")
        if self.code is FailureCode.SKIPPED_AFTER_FAILURE and self.caused_by_check_id is None:
            raise ValueError("skipped failures require caused_by_check_id")
        return self


class FailureSummary(StrictModel):
    """Deterministic aggregate of check-level failure records for one run."""

    schema_version: Literal["0.1"] = "0.1"
    total: int = Field(default=0, ge=0)
    evaluation_failures: int = Field(default=0, ge=0)
    infrastructure_errors: int = Field(default=0, ge=0)
    not_evaluated: int = Field(default=0, ge=0)
    by_category: dict[str, int] = Field(default_factory=dict)
    by_code: dict[str, int] = Field(default_factory=dict)
    primary_check_id: str | None = None
    primary_code: FailureCode | None = None

    @model_validator(mode="after")
    def counts_must_be_consistent(self) -> FailureSummary:
        impacts = self.evaluation_failures + self.infrastructure_errors + self.not_evaluated
        if impacts != self.total:
            raise ValueError("failure impact counts must sum to total")
        if sum(self.by_category.values()) != self.total:
            raise ValueError("failure category counts must sum to total")
        if sum(self.by_code.values()) != self.total:
            raise ValueError("failure code counts must sum to total")
        if (self.primary_check_id is None) != (self.primary_code is None):
            raise ValueError("primary_check_id and primary_code must be set together")
        return self


def _record(
    category: FailureCategory,
    code: FailureCode,
    impact: FailureImpact,
    retryability: Retryability,
    summary: str,
    *,
    details: dict[str, Any] | None = None,
    caused_by_check_id: str | None = None,
    causes: list[FailureCode] | None = None,
) -> FailureRecord:
    return FailureRecord(
        category=category,
        code=code,
        impact=impact,
        retryability=retryability,
        summary=summary,
        details=details or {},
        caused_by_check_id=caused_by_check_id,
        causes=causes or [],
    )


def unexpected_exit_failure(
    actual_exit_code: int,
    expected_exit_codes: list[int],
) -> FailureRecord:
    """Classify a completed process whose exit code violated the check contract."""
    if actual_exit_code < 0:
        return _record(
            FailureCategory.TASK,
            FailureCode.SIGNAL_TERMINATION,
            FailureImpact.EVALUATION_FAILURE,
            Retryability.NO,
            "command was terminated by a signal",
            details={
                "signal": -actual_exit_code,
                "actual_exit_code": actual_exit_code,
                "expected_exit_codes": expected_exit_codes,
            },
        )
    return _record(
        FailureCategory.TASK,
        FailureCode.UNEXPECTED_EXIT,
        FailureImpact.EVALUATION_FAILURE,
        Retryability.NO,
        "command returned an unexpected exit code",
        details={
            "actual_exit_code": actual_exit_code,
            "expected_exit_codes": expected_exit_codes,
        },
    )


def timeout_failure(
    timeout_seconds: float,
    backend: str,
    *,
    infrastructure_code: FailureCode | None = None,
) -> FailureRecord:
    """Classify a timeout and any typed infrastructure complication."""
    if infrastructure_code is FailureCode.SANDBOX_CLEANUP:
        return _record(
            FailureCategory.SANDBOX,
            FailureCode.SANDBOX_CLEANUP,
            FailureImpact.INFRASTRUCTURE_ERROR,
            Retryability.AFTER_FIX,
            "timed-out container could not be cleaned up safely",
            details={"timeout_seconds": timeout_seconds, "backend": backend},
            causes=[FailureCode.TIMEOUT],
        )
    if infrastructure_code is FailureCode.OUTPUT_CAPTURE:
        return _record(
            FailureCategory.OBSERVABILITY,
            FailureCode.OUTPUT_CAPTURE,
            FailureImpact.INFRASTRUCTURE_ERROR,
            Retryability.AFTER_FIX,
            "command timed out and output capture became unreliable",
            details={"timeout_seconds": timeout_seconds, "backend": backend},
            causes=[FailureCode.TIMEOUT],
        )
    return _record(
        FailureCategory.RESOURCE,
        FailureCode.TIMEOUT,
        FailureImpact.EVALUATION_FAILURE,
        Retryability.MAYBE,
        "command exceeded its declared time budget",
        details={"timeout_seconds": timeout_seconds, "backend": backend},
    )


def output_capture_failure(backend: str) -> FailureRecord:
    return _record(
        FailureCategory.OBSERVABILITY,
        FailureCode.OUTPUT_CAPTURE,
        FailureImpact.INFRASTRUCTURE_ERROR,
        Retryability.AFTER_FIX,
        "command output could not be captured reliably",
        details={"backend": backend},
    )


def working_directory_failure() -> FailureRecord:
    return _record(
        FailureCategory.ENVIRONMENT,
        FailureCode.WORKING_DIRECTORY_MISSING,
        FailureImpact.INFRASTRUCTURE_ERROR,
        Retryability.AFTER_FIX,
        "declared check working directory does not exist",
    )


def sandbox_failure(*, configuration: bool = False) -> FailureRecord:
    code = FailureCode.SANDBOX_CONFIGURATION if configuration else FailureCode.SANDBOX_RUNTIME
    summary = (
        "sandbox configuration is incomplete"
        if configuration
        else "sandbox runtime could not execute the check"
    )
    return _record(
        FailureCategory.SANDBOX,
        code,
        FailureImpact.INFRASTRUCTURE_ERROR,
        Retryability.AFTER_FIX,
        summary,
    )


def launch_failure(exc: OSError, backend: str) -> FailureRecord:
    """Classify a typed launch exception without parsing its message."""
    details: dict[str, Any] = {"backend": backend}
    if exc.errno is not None:
        details["errno"] = exc.errno
    if isinstance(exc, FileNotFoundError) or exc.errno == errno.ENOENT:
        return _record(
            FailureCategory.DEPENDENCY,
            FailureCode.COMMAND_NOT_FOUND,
            FailureImpact.INFRASTRUCTURE_ERROR,
            Retryability.AFTER_FIX,
            "command executable was not found",
            details=details,
        )
    if isinstance(exc, PermissionError) or exc.errno in {errno.EACCES, errno.EPERM}:
        return _record(
            FailureCategory.ENVIRONMENT,
            FailureCode.PERMISSION_DENIED,
            FailureImpact.INFRASTRUCTURE_ERROR,
            Retryability.AFTER_FIX,
            "command could not be started because permission was denied",
            details=details,
        )
    return _record(
        FailureCategory.ENVIRONMENT,
        FailureCode.PROCESS_LAUNCH_ERROR,
        FailureImpact.INFRASTRUCTURE_ERROR,
        Retryability.UNKNOWN,
        "command process could not be started",
        details=details,
    )


def skipped_failure(blocked_by_check_id: str) -> FailureRecord:
    return _record(
        FailureCategory.CONTROL_FLOW,
        FailureCode.SKIPPED_AFTER_FAILURE,
        FailureImpact.NOT_EVALUATED,
        Retryability.AFTER_FIX,
        "check was skipped after an earlier check failed",
        caused_by_check_id=blocked_by_check_id,
    )


def summarize_failures(
    records: Iterable[tuple[str, FailureRecord | None]],
) -> FailureSummary:
    """Aggregate ordered failures with infrastructure-first primary selection."""
    present: list[tuple[int, str, FailureRecord]] = []
    for index, (check_id, failure) in enumerate(records):
        if failure is not None:
            present.append((index, check_id, failure))
    if not present:
        return FailureSummary()
    category_counts: Counter[str] = Counter(
        failure.category.value for _, _, failure in present
    )
    code_counts: Counter[str] = Counter(failure.code.value for _, _, failure in present)
    impact_counts: Counter[FailureImpact] = Counter(
        failure.impact for _, _, failure in present
    )
    rank = {
        FailureImpact.INFRASTRUCTURE_ERROR: 0,
        FailureImpact.EVALUATION_FAILURE: 1,
        FailureImpact.NOT_EVALUATED: 2,
    }
    _, primary_check_id, primary = min(
        present,
        key=lambda item: (rank[item[2].impact], item[0]),
    )
    return FailureSummary(
        total=len(present),
        evaluation_failures=impact_counts[FailureImpact.EVALUATION_FAILURE],
        infrastructure_errors=impact_counts[FailureImpact.INFRASTRUCTURE_ERROR],
        not_evaluated=impact_counts[FailureImpact.NOT_EVALUATED],
        by_category=dict(sorted(category_counts.items())),
        by_code=dict(sorted(code_counts.items())),
        primary_check_id=primary_check_id,
        primary_code=primary.code,
    )
