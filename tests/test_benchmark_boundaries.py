from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from e2h.benchmark import (
    BenchmarkError,
    FailurePattern,
    FailurePatternCorpus,
    PatternOrigin,
    SanitizationAction,
    SanitizationAttestation,
    failure_pattern_corpus_sha256,
    verify_failure_pattern_corpus,
)
from e2h.failures import FailureCategory, FailureCode

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _CorpusSubclass(FailurePatternCorpus):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def pattern(*, identifier: str = "timeout-pattern") -> FailurePattern:
    return FailurePattern(
        id=identifier,
        title="Bounded timeout pattern",
        origin=PatternOrigin.SYNTHETIC,
        failure_code=FailureCode.TIMEOUT,
        category=FailureCategory.RESOURCE,
        scenario="A bounded operation exceeds its declared time budget.",
        observable_signals=["The operation reaches its timeout boundary."],
        expected_behavior="Classify the observation as a timeout.",
        sanitization=SanitizationAttestation(
            actions=[SanitizationAction.REDUCED_TO_OBSERVABLE_SIGNALS]
        ),
    )


def corpus() -> FailurePatternCorpus:
    return FailurePatternCorpus(
        id="boundary-corpus",
        title="Boundary corpus",
        patterns=[pattern()],
        metadata={"purpose": "boundary"},
    )


def test_digest_rejects_corpus_subclass_and_lookalike() -> None:
    candidate = corpus()
    subclassed = _CorpusSubclass.model_validate(candidate.model_dump(mode="json"))
    lookalike = _Lookalike.model_validate(candidate.model_dump(mode="json"))

    with pytest.raises(
        BenchmarkError,
        match="expected FailurePatternCorpus, got _CorpusSubclass",
    ):
        failure_pattern_corpus_sha256(subclassed)

    with pytest.raises(
        BenchmarkError,
        match="expected FailurePatternCorpus, got _Lookalike",
    ):
        failure_pattern_corpus_sha256(cast(Any, lookalike))


def test_verification_rejects_corpus_subclass() -> None:
    candidate = corpus()
    subclassed = _CorpusSubclass.model_validate(candidate.model_dump(mode="json"))

    with pytest.raises(
        BenchmarkError,
        match="expected FailurePatternCorpus, got _CorpusSubclass",
    ):
        verify_failure_pattern_corpus(subclassed)


def test_boundary_revalidates_duplicate_pattern_ids() -> None:
    candidate = corpus()
    candidate.patterns.append(pattern(identifier="second"))
    candidate.patterns[1].id = candidate.patterns[0].id

    with pytest.raises(BenchmarkError, match="failure pattern ids must be unique"):
        verify_failure_pattern_corpus(candidate)


def test_boundary_revalidates_taxonomy_mutation() -> None:
    candidate = corpus()
    candidate.patterns[0].category = FailureCategory.TASK

    with pytest.raises(BenchmarkError, match="requires category 'resource'"):
        failure_pattern_corpus_sha256(candidate)


def test_boundary_revalidates_origin_source_mutation() -> None:
    candidate = corpus()
    candidate.patterns[0].origin = PatternOrigin.SANITIZED_REAL_WORLD

    with pytest.raises(BenchmarkError, match="require a public source"):
        verify_failure_pattern_corpus(candidate)


def test_boundary_revalidates_sanitization_action_mutation() -> None:
    candidate = corpus()
    action = candidate.patterns[0].sanitization.actions[0]
    candidate.patterns[0].sanitization.actions.append(action)

    with pytest.raises(BenchmarkError, match="sanitization actions must be unique"):
        verify_failure_pattern_corpus(candidate)


def test_boundary_preserves_canonical_invalid_metadata_for_rejection() -> None:
    candidate = corpus()
    candidate.metadata = {"invalid": {"set-value"}}

    with pytest.raises(BenchmarkError, match="canonical JSON"):
        failure_pattern_corpus_sha256(candidate)


def test_boundary_normalizes_warning_prone_raw_pattern_assignment() -> None:
    candidate = corpus()
    expected_digest = failure_pattern_corpus_sha256(candidate)
    candidate.patterns = [candidate.patterns[0].model_dump(mode="json")]

    assert failure_pattern_corpus_sha256(candidate) == expected_digest
    verification = verify_failure_pattern_corpus(candidate)
    assert verification.verified is True
    assert verification.pattern_count == 1
