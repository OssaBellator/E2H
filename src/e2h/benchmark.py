"""Versioned community benchmark corpus for sanitized observable failure patterns."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import load_mapping_document
from e2h.failures import FailureCategory, FailureCode
from e2h.privacy import RedactionPolicyError, apply_redaction_policy
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

_MAX_CORPUS_BYTES = 4 * 1024 * 1024
_MAX_PATTERNS = 1_000
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"

_CODE_CATEGORY = {
    FailureCode.UNEXPECTED_EXIT: FailureCategory.TASK,
    FailureCode.SIGNAL_TERMINATION: FailureCategory.TASK,
    FailureCode.TIMEOUT: FailureCategory.RESOURCE,
    FailureCode.COMMAND_NOT_FOUND: FailureCategory.DEPENDENCY,
    FailureCode.PERMISSION_DENIED: FailureCategory.ENVIRONMENT,
    FailureCode.PROCESS_LAUNCH_ERROR: FailureCategory.ENVIRONMENT,
    FailureCode.WORKING_DIRECTORY_MISSING: FailureCategory.ENVIRONMENT,
    FailureCode.SANDBOX_CONFIGURATION: FailureCategory.SANDBOX,
    FailureCode.SANDBOX_RUNTIME: FailureCategory.SANDBOX,
    FailureCode.SANDBOX_CLEANUP: FailureCategory.SANDBOX,
    FailureCode.OUTPUT_CAPTURE: FailureCategory.OBSERVABILITY,
    FailureCode.SKIPPED_AFTER_FAILURE: FailureCategory.CONTROL_FLOW,
}


class BenchmarkError(ValueError):
    """Raised when a benchmark corpus cannot be loaded or verified safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatternOrigin(StrEnum):
    """Whether a benchmark pattern is derived from an observed public incident."""

    SANITIZED_REAL_WORLD = "sanitized_real_world"
    SYNTHETIC = "synthetic"


class SanitizationAction(StrEnum):
    """Non-reversible transformations allowed for community benchmark publication."""

    PARAPHRASED = "paraphrased"
    REMOVED_IDENTIFIERS = "removed_identifiers"
    NORMALIZED_PATHS = "normalized_paths"
    OMITTED_RAW_LOGS = "omitted_raw_logs"
    REMOVED_ENVIRONMENT_VALUES = "removed_environment_values"
    REDUCED_TO_OBSERVABLE_SIGNALS = "reduced_to_observable_signals"


class PublicSource(StrictModel):
    """Public provenance reference used to audit a sanitized real-world pattern."""

    reference: str = Field(min_length=1, max_length=4096)
    accessed_at: date
    source_kind: Literal["public_issue", "public_discussion", "public_report"]

    @field_validator("reference")
    @classmethod
    def reference_must_be_public_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("public source reference must be an absolute HTTPS URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("public source reference must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("public source reference must not contain query or fragment data")
        return value


class SanitizationAttestation(StrictModel):
    """Machine-checkable publication boundary for a benchmark pattern."""

    raw_source_text_included: Literal[False] = False
    raw_logs_included: Literal[False] = False
    direct_identifiers_included: Literal[False] = False
    secrets_included: Literal[False] = False
    manual_reviewed: Literal[True] = True
    actions: list[SanitizationAction] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def actions_must_be_unique(self) -> SanitizationAttestation:
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("sanitization actions must be unique")
        return self


class FailurePattern(StrictModel):
    """One sanitized pattern expressed only through observable failure semantics."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=160)
    origin: PatternOrigin
    failure_code: FailureCode
    category: FailureCategory
    scenario: str = Field(min_length=1, max_length=2_000)
    observable_signals: list[str] = Field(min_length=1, max_length=20)
    expected_behavior: str = Field(min_length=1, max_length=1_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source: PublicSource | None = None
    sanitization: SanitizationAttestation

    @field_validator("observable_signals")
    @classmethod
    def signals_must_be_bounded(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("observable signals must contain 1 to 500 characters")
        if len(set(values)) != len(values):
            raise ValueError("observable signals must be unique")
        return values

    @field_validator("tags")
    @classmethod
    def tags_must_be_bounded(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 64 for value in values):
            raise ValueError("tags must contain 1 to 64 characters")
        if len(set(values)) != len(values):
            raise ValueError("tags must be unique")
        return values

    @model_validator(mode="after")
    def taxonomy_and_provenance_must_be_consistent(self) -> FailurePattern:
        expected_category = _CODE_CATEGORY[self.failure_code]
        if self.category is not expected_category:
            raise ValueError(
                f"failure code {self.failure_code.value!r} requires category "
                f"{expected_category.value!r}"
            )
        if self.origin is PatternOrigin.SANITIZED_REAL_WORLD and self.source is None:
            raise ValueError("sanitized real-world patterns require a public source")
        if self.origin is PatternOrigin.SYNTHETIC and self.source is not None:
            raise ValueError("synthetic patterns must not claim public real-world provenance")
        return self


class FailurePatternCorpus(StrictModel):
    """Versioned collection of sanitized failure patterns for community benchmarking."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=255)
    patterns: list[FailurePattern] = Field(min_length=1, max_length=_MAX_PATTERNS)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def pattern_ids_must_be_unique(self) -> FailurePatternCorpus:
        identifiers = [pattern.id for pattern in self.patterns]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("failure pattern ids must be unique")
        try:
            json.dumps(self.metadata, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("benchmark metadata must contain canonical JSON values") from exc
        return self


def _validated_failure_pattern_corpus(
    corpus: FailurePatternCorpus,
) -> FailurePatternCorpus:
    if type(corpus) is not FailurePatternCorpus:
        raise BenchmarkError(
            "invalid benchmark corpus: expected FailurePatternCorpus, "
            f"got {type(corpus).__name__}"
        )
    try:
        payload = corpus.model_dump(mode="python", warnings="none")
        return FailurePatternCorpus.model_validate(payload)
    except ValueError as exc:
        raise BenchmarkError(f"invalid benchmark corpus: {exc}") from exc


class FailurePatternVerification(StrictModel):
    """Privacy and provenance verification for a benchmark corpus."""

    schema_version: Literal["0.1"] = "0.1"
    corpus_id: str = Field(pattern=_ID_PATTERN)
    corpus_sha256: str = Field(pattern=_SHA256_PATTERN)
    pattern_count: int = Field(ge=1)
    sanitized_real_world_count: int = Field(ge=0)
    synthetic_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    by_category: dict[str, int]
    by_code: dict[str, int]
    privacy_findings: int = Field(ge=0)
    verified: bool


def failure_pattern_corpus_sha256(corpus: FailurePatternCorpus) -> str:
    """Return the canonical digest of one normalized benchmark corpus."""
    corpus = _validated_failure_pattern_corpus(corpus)
    payload = json.dumps(
        corpus.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _privacy_trace(corpus: FailurePatternCorpus) -> Trace:
    timestamp = datetime(2000, 1, 1, tzinfo=UTC)
    context = TraceContext(run_id="benchmark-privacy", capsule_id=corpus.id)
    events = [
        TraceEvent(
            trace_id="benchmark-privacy",
            sequence=0,
            event_type=TraceEventType.RUN_STARTED,
            timestamp=timestamp,
            context=context,
            payload={"corpus_id": corpus.id, "metadata": corpus.metadata},
        )
    ]
    for pattern in corpus.patterns:
        events.append(
            TraceEvent(
                trace_id="benchmark-privacy",
                sequence=len(events),
                event_type=TraceEventType.ARTIFACT_OBSERVED,
                timestamp=timestamp,
                context=context,
                payload={
                    "id": pattern.id,
                    "title": pattern.title,
                    "scenario": pattern.scenario,
                    "observable_signals": pattern.observable_signals,
                    "expected_behavior": pattern.expected_behavior,
                    "tags": pattern.tags,
                },
            )
        )
    events.append(
        TraceEvent(
            trace_id="benchmark-privacy",
            sequence=len(events),
            event_type=TraceEventType.RUN_COMPLETED,
            timestamp=timestamp,
            context=context,
            payload={"pattern_count": len(corpus.patterns)},
        )
    )
    return Trace(trace_id="benchmark-privacy", events=events)


def verify_failure_pattern_corpus(corpus: FailurePatternCorpus) -> FailurePatternVerification:
    """Verify provenance claims and reject residual common PII/secret patterns."""
    corpus = _validated_failure_pattern_corpus(corpus)
    try:
        outcome = apply_redaction_policy(
            [_privacy_trace(corpus)],
            redaction_enabled=False,
            max_records=1_000,
        )
    except RedactionPolicyError as exc:
        raise BenchmarkError(str(exc)) from exc
    findings = len(outcome.review.residual_findings)
    origin_counts = Counter(pattern.origin for pattern in corpus.patterns)
    category_counts = Counter(pattern.category.value for pattern in corpus.patterns)
    code_counts = Counter(pattern.failure_code.value for pattern in corpus.patterns)
    source_count = sum(pattern.source is not None for pattern in corpus.patterns)
    verified = findings == 0
    return FailurePatternVerification(
        corpus_id=corpus.id,
        corpus_sha256=failure_pattern_corpus_sha256(corpus),
        pattern_count=len(corpus.patterns),
        sanitized_real_world_count=origin_counts[PatternOrigin.SANITIZED_REAL_WORLD],
        synthetic_count=origin_counts[PatternOrigin.SYNTHETIC],
        source_count=source_count,
        by_category=dict(sorted(category_counts.items())),
        by_code=dict(sorted(code_counts.items())),
        privacy_findings=findings,
        verified=verified,
    )


def load_failure_pattern_corpus(path: Path) -> FailurePatternCorpus:
    """Load one bounded strict JSON/YAML failure-pattern corpus."""
    try:
        payload = load_mapping_document(
            path,
            noun="benchmark corpus",
            max_bytes=_MAX_CORPUS_BYTES,
        )
        return FailurePatternCorpus.model_validate(payload)
    except ValueError as exc:
        if isinstance(exc, BenchmarkError):
            raise
        raise BenchmarkError(f"invalid benchmark corpus: {exc}") from exc
