"""Configurable privacy policies and non-reversible redaction review reports."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.trace import Trace

_MAX_POLICY_BYTES = 256 * 1024
_MAX_REDACTIONS = 10_000
_MAX_CUSTOM_RULES = 100
_MAX_ALLOW_VALUES = 1_000
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}\b")
_PHONE = re.compile(
    r"(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)|\d{2,4})[ .-]\d{3,4}[ .-]\d{3,4}(?!\w)"
)
_TOKEN_PATTERNS = (
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/=-]{12,})")
_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|secret|password)\b\s*[:=]\s*[\"']?)"
    r"([^\s,\"']{8,})"
)


class RedactionPolicyError(ValueError):
    """Raised when a privacy policy cannot be loaded or applied safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RedactionKind(StrEnum):
    """Classes of sensitive values recognized by the privacy engine."""

    SECRET = "secret"
    EMAIL = "email"
    PHONE = "phone"
    CUSTOM = "custom"


class RedactionRecord(StrictModel):
    """Non-reversible record of one removed sensitive value."""

    kind: RedactionKind
    location: str
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    placeholder: str
    rule_id: str | None = Field(default=None, pattern=_ID_PATTERN)


class CustomRedactionRule(StrictModel):
    """Trusted regular-expression rule applied before built-in detectors."""

    id: str = Field(pattern=_ID_PATTERN)
    pattern: str = Field(min_length=1, max_length=512)
    ignore_case: bool = False
    multiline: bool = False

    @model_validator(mode="after")
    def pattern_must_compile(self) -> CustomRedactionRule:
        try:
            re.compile(self.pattern, self.flags)
        except re.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return self

    @property
    def flags(self) -> int:
        flags = 0
        if self.ignore_case:
            flags |= re.IGNORECASE
        if self.multiline:
            flags |= re.MULTILINE
        return flags

    def compile(self) -> re.Pattern[str]:
        return re.compile(self.pattern, self.flags)


class RedactionPolicy(StrictModel):
    """Versioned redaction and review policy."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(default="default", pattern=_ID_PATTERN)
    redact_secrets: bool = True
    redact_emails: bool = True
    redact_phones: bool = True
    custom_rules: list[CustomRedactionRule] = Field(
        default_factory=list,
        max_length=_MAX_CUSTOM_RULES,
    )
    allow_values: list[str] = Field(default_factory=list, max_length=_MAX_ALLOW_VALUES)

    @field_validator("allow_values")
    @classmethod
    def allow_values_must_be_bounded(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 4_096 for value in values):
            raise ValueError("allow_values entries must contain 1 to 4096 characters")
        if len(set(values)) != len(values):
            raise ValueError("allow_values entries must be unique")
        if sum(len(value) for value in values) > 100_000:
            raise ValueError("allow_values exceed the 100000 character policy limit")
        return values

    @model_validator(mode="after")
    def custom_rule_ids_must_be_unique(self) -> RedactionPolicy:
        identifiers = [rule.id for rule in self.custom_rules]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("custom rule ids must be unique")
        return self


class ResidualFinding(StrictModel):
    """Hashed sensitive-looking value still present after policy application."""

    kind: RedactionKind
    location: str
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    detector: str
    rule_id: str | None = Field(default=None, pattern=_ID_PATTERN)


class RedactionReview(StrictModel):
    """Privacy review summary that never stores raw matched values."""

    schema_version: Literal["0.1"] = "0.1"
    policy_id: str = Field(pattern=_ID_PATTERN)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redaction_enabled: bool
    total_redactions: int = Field(ge=0)
    unique_redacted_values: int = Field(ge=0)
    counts_by_kind: dict[str, int] = Field(default_factory=dict)
    counts_by_rule: dict[str, int] = Field(default_factory=dict)
    allow_value_count: int = Field(ge=0)
    residual_findings: list[ResidualFinding] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    manual_review_recommended: bool


@dataclass(frozen=True)
class RedactionOutcome:
    traces: list[Trace]
    records: list[RedactionRecord]
    review: RedactionReview


def default_redaction_policy() -> RedactionPolicy:
    """Return the stable built-in baseline policy."""
    return RedactionPolicy()


def redaction_policy_sha256(policy: RedactionPolicy) -> str:
    """Return the digest of the canonical policy document."""
    payload = json.dumps(
        policy.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def load_redaction_policy(path: Path) -> RedactionPolicy:
    """Load a strict JSON or YAML redaction policy."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RedactionPolicyError(f"unable to read redaction policy: {exc}") from exc
    if len(raw) > _MAX_POLICY_BYTES:
        raise RedactionPolicyError(f"redaction policy exceeds {_MAX_POLICY_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RedactionPolicyError("redaction policy must be UTF-8") from exc
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(text, parse_constant=_reject_json_constant)
        elif suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(text)
        else:
            raise RedactionPolicyError("redaction policy must use .json, .yaml, or .yml")
        if not isinstance(data, dict):
            raise RedactionPolicyError("redaction policy root must be an object")
        return RedactionPolicy.model_validate(data)
    except RedactionPolicyError:
        raise
    except (ValueError, yaml.YAMLError) as exc:
        raise RedactionPolicyError(f"invalid redaction policy: {exc}") from exc


def _pointer_child(location: str, key: str) -> str:
    escaped = key.replace("~", "~0").replace("/", "~1")
    return f"{location}/{escaped}"


def _is_placeholder(value: str) -> bool:
    return value.startswith("<redacted:") and value.endswith(">")


def _keep_match(raw: str, allowed: frozenset[str]) -> bool:
    return raw in allowed or _is_placeholder(raw)


def _placeholder(
    kind: RedactionKind,
    raw: str,
    location: str,
    records: list[RedactionRecord],
    *,
    rule_id: str | None = None,
) -> str:
    if len(records) >= _MAX_REDACTIONS:
        raise RedactionPolicyError(f"evidence exceeds {_MAX_REDACTIONS} redactions")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    label = f"custom:{rule_id}" if kind is RedactionKind.CUSTOM else kind.value
    value = f"<redacted:{label}:{digest[:12]}>"
    records.append(
        RedactionRecord(
            kind=kind,
            location=location,
            digest=digest,
            placeholder=value,
            rule_id=rule_id,
        )
    )
    return value


def _replace_match(
    match: re.Match[str],
    *,
    kind: RedactionKind,
    location: str,
    records: list[RedactionRecord],
    allowed: frozenset[str],
    rule_id: str | None = None,
    group: int = 0,
) -> str:
    raw = match.group(group)
    if _keep_match(raw, allowed):
        return match.group(0)
    replacement = _placeholder(kind, raw, location, records, rule_id=rule_id)
    if group == 0:
        return replacement
    return match.group(0)[: match.start(group) - match.start(0)] + replacement


def _redact_text(
    value: str,
    location: str,
    records: list[RedactionRecord],
    policy: RedactionPolicy,
    custom_patterns: list[tuple[CustomRedactionRule, re.Pattern[str]]],
    allowed: frozenset[str],
) -> str:
    rendered = value
    for rule, pattern in custom_patterns:
        rendered = pattern.sub(
            lambda match, rule=rule: _replace_match(
                match,
                kind=RedactionKind.CUSTOM,
                location=location,
                records=records,
                allowed=allowed,
                rule_id=rule.id,
            ),
            rendered,
        )
    if policy.redact_secrets:
        rendered = _ASSIGNMENT.sub(
            lambda match: _replace_match(
                match,
                kind=RedactionKind.SECRET,
                location=location,
                records=records,
                allowed=allowed,
                group=2,
            ),
            rendered,
        )
        rendered = _BEARER.sub(
            lambda match: _replace_match(
                match,
                kind=RedactionKind.SECRET,
                location=location,
                records=records,
                allowed=allowed,
                group=2,
            ),
            rendered,
        )
        for _, pattern in _TOKEN_PATTERNS:
            rendered = pattern.sub(
                lambda match: _replace_match(
                    match,
                    kind=RedactionKind.SECRET,
                    location=location,
                    records=records,
                    allowed=allowed,
                ),
                rendered,
            )
    if policy.redact_emails:
        rendered = _EMAIL.sub(
            lambda match: _replace_match(
                match,
                kind=RedactionKind.EMAIL,
                location=location,
                records=records,
                allowed=allowed,
            ),
            rendered,
        )
    if policy.redact_phones:
        rendered = _PHONE.sub(
            lambda match: _replace_match(
                match,
                kind=RedactionKind.PHONE,
                location=location,
                records=records,
                allowed=allowed,
            ),
            rendered,
        )
    return rendered


def _redact_value(
    value: Any,
    location: str,
    records: list[RedactionRecord],
    policy: RedactionPolicy,
    custom_patterns: list[tuple[CustomRedactionRule, re.Pattern[str]]],
    allowed: frozenset[str],
) -> Any:
    if isinstance(value, str):
        return _redact_text(value, location, records, policy, custom_patterns, allowed)
    if isinstance(value, list):
        return [
            _redact_value(
                item,
                _pointer_child(location, str(index)),
                records,
                policy,
                custom_patterns,
                allowed,
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            string_key = str(key)
            key_location = f"{location}/<key:{index}>"
            redacted_key = _redact_text(
                string_key,
                key_location,
                records,
                policy,
                custom_patterns,
                allowed,
            )
            if redacted_key in redacted:
                raise RedactionPolicyError("redaction produced duplicate object keys")
            redacted[redacted_key] = _redact_value(
                item,
                _pointer_child(location, redacted_key),
                records,
                policy,
                custom_patterns,
                allowed,
            )
        return redacted
    return value


def _redact_trace(
    trace: Trace,
    records: list[RedactionRecord],
    *,
    trace_index: int,
    policy: RedactionPolicy,
    custom_patterns: list[tuple[CustomRedactionRule, re.Pattern[str]]],
    allowed: frozenset[str],
) -> Trace:
    copy = trace.model_copy(deep=True)
    for event in copy.events:
        prefix = f"/traces/{trace_index}/events/{event.sequence}"
        event.context.metadata = cast(
            dict[str, Any],
            _redact_value(
                event.context.metadata,
                f"{prefix}/context/metadata",
                records,
                policy,
                custom_patterns,
                allowed,
            ),
        )
        event.attributes = cast(
            dict[str, Any],
            _redact_value(
                event.attributes,
                f"{prefix}/attributes",
                records,
                policy,
                custom_patterns,
                allowed,
            ),
        )
        event.payload = cast(
            dict[str, Any],
            _redact_value(
                event.payload,
                f"{prefix}/payload",
                records,
                policy,
                custom_patterns,
                allowed,
            ),
        )
    return Trace.model_validate(copy.model_dump())


def _iter_strings(value: Any, location: str):
    if isinstance(value, str):
        yield location, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _iter_strings(item, _pointer_child(location, str(index)))
    elif isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            yield f"{location}/<key:{index}>", str(key)
            yield from _iter_strings(item, _pointer_child(location, str(key)))


def _candidate_matches(
    value: str,
    custom_patterns: list[tuple[CustomRedactionRule, re.Pattern[str]]],
):
    for match in _ASSIGNMENT.finditer(value):
        yield RedactionKind.SECRET, "assignment", None, match.group(2)
    for match in _BEARER.finditer(value):
        yield RedactionKind.SECRET, "bearer", None, match.group(2)
    for detector, pattern in _TOKEN_PATTERNS:
        for match in pattern.finditer(value):
            yield RedactionKind.SECRET, detector, None, match.group(0)
    for match in _EMAIL.finditer(value):
        yield RedactionKind.EMAIL, "email", None, match.group(0)
    for match in _PHONE.finditer(value):
        yield RedactionKind.PHONE, "phone", None, match.group(0)
    for rule, pattern in custom_patterns:
        for match in pattern.finditer(value):
            yield RedactionKind.CUSTOM, f"custom:{rule.id}", rule.id, match.group(0)


def _scan_residuals(
    traces: list[Trace],
    custom_patterns: list[tuple[CustomRedactionRule, re.Pattern[str]]],
    allowed: frozenset[str],
) -> list[ResidualFinding]:
    findings: list[ResidualFinding] = []
    seen: set[tuple[RedactionKind, str | None, str, str]] = set()
    for trace_index, trace in enumerate(traces):
        for event in trace.events:
            prefix = f"/traces/{trace_index}/events/{event.sequence}"
            sections = (
                (f"{prefix}/context/metadata", event.context.metadata),
                (f"{prefix}/attributes", event.attributes),
                (f"{prefix}/payload", event.payload),
            )
            for section, payload in sections:
                for location, text in _iter_strings(payload, section):
                    for kind, detector, rule_id, raw in _candidate_matches(
                        text,
                        custom_patterns,
                    ):
                        if _keep_match(raw, allowed):
                            continue
                        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                        key = (kind, rule_id, location, digest)
                        if key in seen:
                            continue
                        if len(findings) >= _MAX_REDACTIONS:
                            raise RedactionPolicyError(
                                f"evidence exceeds {_MAX_REDACTIONS} residual findings"
                            )
                        seen.add(key)
                        findings.append(
                            ResidualFinding(
                                kind=kind,
                                location=location,
                                digest=digest,
                                detector=detector,
                                rule_id=rule_id,
                            )
                        )
    return findings


def apply_redaction_policy(
    traces: list[Trace],
    *,
    policy: RedactionPolicy | None = None,
    redaction_enabled: bool = True,
) -> RedactionOutcome:
    """Apply a policy and produce a non-reversible review report."""
    active_policy = policy or default_redaction_policy()
    custom_patterns = [(rule, rule.compile()) for rule in active_policy.custom_rules]
    allowed = frozenset(active_policy.allow_values)
    records: list[RedactionRecord] = []
    processed = traces
    if redaction_enabled:
        processed = [
            _redact_trace(
                trace,
                records,
                trace_index=index,
                policy=active_policy,
                custom_patterns=custom_patterns,
                allowed=allowed,
            )
            for index, trace in enumerate(traces)
        ]
    residuals = _scan_residuals(processed, custom_patterns, allowed)
    kind_counts = Counter(record.kind.value for record in records)
    rule_counts = Counter(record.rule_id for record in records if record.rule_id is not None)
    warnings = ["pattern_matching_cannot_prove_complete_deidentification"]
    if not redaction_enabled:
        warnings.append("redaction_disabled_review_only")
    if active_policy.custom_rules:
        warnings.append("custom_rules_are_trusted_python_regular_expressions")
    if active_policy.allow_values:
        warnings.append("allowlisted_values_are_retained_verbatim")
    if residuals:
        warnings.append("residual_sensitive_patterns_detected")
    review = RedactionReview(
        policy_id=active_policy.id,
        policy_sha256=redaction_policy_sha256(active_policy),
        redaction_enabled=redaction_enabled,
        total_redactions=len(records),
        unique_redacted_values=len({record.digest for record in records}),
        counts_by_kind=dict(sorted(kind_counts.items())),
        counts_by_rule=dict(sorted(rule_counts.items())),
        allow_value_count=len(active_policy.allow_values),
        residual_findings=residuals,
        warnings=warnings,
        manual_review_recommended=True,
    )
    return RedactionOutcome(traces=processed, records=records, review=review)
