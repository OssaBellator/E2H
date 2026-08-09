from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from e2h.benchmark import (
    BenchmarkError,
    FailurePattern,
    FailurePatternCorpus,
    PatternOrigin,
    SanitizationAction,
    SanitizationAttestation,
    failure_pattern_corpus_sha256,
)
from e2h.failures import FailureCategory, FailureCode


def _pattern() -> FailurePattern:
    return FailurePattern(
        id="pattern",
        title="Unexpected exit",
        origin=PatternOrigin.SYNTHETIC,
        failure_code=FailureCode.UNEXPECTED_EXIT,
        category=FailureCategory.TASK,
        scenario="A deterministic command exits with a non-zero status.",
        observable_signals=["non-zero exit status"],
        expected_behavior="Record the task failure deterministically.",
        sanitization=SanitizationAttestation(actions=[SanitizationAction.PARAPHRASED]),
    )


def _corpus(metadata: dict[str, Any] | None = None) -> FailurePatternCorpus:
    return FailurePatternCorpus(
        id="corpus",
        title="Corpus",
        patterns=[_pattern()],
        metadata={} if metadata is None else metadata,
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_benchmark_rejects_json_coercible_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="canonical JSON values"):
        _corpus(metadata)


def test_benchmark_digest_revalidates_mutated_metadata() -> None:
    corpus = _corpus()
    corpus.metadata["nested"] = (1, 2)

    with pytest.raises(BenchmarkError, match="invalid benchmark corpus"):
        failure_pattern_corpus_sha256(corpus)


def test_benchmark_preserves_valid_nested_json_metadata() -> None:
    metadata = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}

    assert _corpus(metadata).metadata == metadata
