from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from e2h.benchmark import (
    FailurePattern,
    PatternOrigin,
    PublicSource,
    SanitizationAction,
    SanitizationAttestation,
)
from e2h.failures import FailureCategory, FailureCode


def _pattern_kwargs() -> dict[str, object]:
    return {
        "id": "pattern",
        "title": "Observed failure",
        "failure_code": FailureCode.UNEXPECTED_EXIT,
        "category": FailureCategory.TASK,
        "scenario": "A command exits unexpectedly.",
        "observable_signals": ["non-zero exit status"],
        "expected_behavior": "Report the task failure.",
    }


def test_failure_pattern_revalidates_mutated_public_source() -> None:
    source = PublicSource(
        reference="https://example.com/issues/1",
        accessed_at=date(2026, 1, 1),
        source_kind="public_issue",
    )
    source.reference = "http://example.com/issues/1"

    with pytest.raises(
        ValidationError,
        match="public source reference must be an absolute HTTPS URL",
    ):
        FailurePattern(
            **_pattern_kwargs(),
            origin=PatternOrigin.SANITIZED_REAL_WORLD,
            source=source,
            sanitization=SanitizationAttestation(
                actions=[SanitizationAction.PARAPHRASED],
            ),
        )


def test_failure_pattern_revalidates_mutated_sanitization() -> None:
    sanitization = SanitizationAttestation(
        actions=[SanitizationAction.PARAPHRASED],
    )
    sanitization.actions = [
        SanitizationAction.PARAPHRASED,
        SanitizationAction.PARAPHRASED,
    ]

    with pytest.raises(ValidationError, match="sanitization actions must be unique"):
        FailurePattern(
            **_pattern_kwargs(),
            origin=PatternOrigin.SYNTHETIC,
            sanitization=sanitization,
        )
