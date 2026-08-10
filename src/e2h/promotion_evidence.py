"""Evidence, policy, and proposal models for promotion decisions."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import _validate_json_compatible
from e2h.partitions import PartitionRole

_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_ID_RE = re.compile(_ID_PATTERN)
_FIELD_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_DOCUMENT_BYTES = 2_097_152
_MAX_METADATA_BYTES = 65_536
_MAX_LOCATOR_CHARS = 2_048


class PromotionError(ValueError):
    """Raised when promotion evidence or rollback metadata is invalid."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        _validate_json_compatible(value)
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must contain canonical JSON data") from exc
    return rendered.encode("utf-8")


def _model_sha256(value: BaseModel) -> str:
    return hashlib.sha256(_canonical_json_bytes(value.model_dump(mode="json"))).hexdigest()


def _validate_metadata(value: dict[str, Any], *, noun: str) -> dict[str, Any]:
    if len(_canonical_json_bytes(value)) > _MAX_METADATA_BYTES:
        raise ValueError(f"{noun} metadata exceeds {_MAX_METADATA_BYTES} bytes")
    return value


def _validate_locator(value: str | None) -> str | None:
    if value is None:
        return None
    if "\x00" in value:
        raise ValueError("rollback target locator must not contain NUL")
    if len(value) > _MAX_LOCATOR_CHARS:
        raise ValueError(f"rollback target locator exceeds {_MAX_LOCATOR_CHARS} characters")
    return value


class PromotionEvidenceRole(StrEnum):
    """Evaluation partitions eligible for promotion decisions."""

    VALIDATION = "validation"
    SEALED_TEST = "sealed_test"

    def partition_role(self) -> PartitionRole:
        return PartitionRole(self.value)


class VariantPrediction(StrictModel):
    """One variant prediction for a partition example."""

    example_id: str = Field(pattern=_ID_PATTERN)
    outputs: dict[str, Any] = Field(min_length=1, max_length=128)

    @field_validator("outputs")
    @classmethod
    def outputs_must_be_canonical(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if _FIELD_RE.fullmatch(key) is None:
                raise ValueError("variant prediction output keys must be Python identifiers")
        _canonical_json_bytes(value)
        return value


class VariantPredictionDocument(StrictModel):
    """Predictions bound to one variant and label-free partition commitments."""

    schema_version: Literal["0.1"] = "0.1"
    variant_id: str = Field(pattern=_ID_PATTERN)
    variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_partition_sha256: str = Field(pattern=_SHA256_PATTERN)
    role: PromotionEvidenceRole
    predictions: list[VariantPrediction] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def prediction_ids_must_be_unique(self) -> VariantPredictionDocument:
        ids = [prediction.example_id for prediction in self.predictions]
        if len(ids) != len(set(ids)):
            raise ValueError("variant prediction example ids must be unique")
        return self


class PairedEvaluationReport(StrictModel):
    """Aggregate paired correctness evidence without labels or case-level outcomes."""

    schema_version: Literal["0.1"] = "0.1"
    evidence_id: str = Field(pattern=_ID_PATTERN)
    role: PromotionEvidenceRole
    dataset_id: str = Field(pattern=_ID_PATTERN)
    partition_id: str = Field(pattern=_ID_PATTERN)
    public_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_partition_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_variant_id: str = Field(pattern=_ID_PATTERN)
    baseline_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_variant_id: str = Field(pattern=_ID_PATTERN)
    candidate_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    total: int = Field(ge=1, le=10_000)
    both_correct: int = Field(ge=0)
    baseline_only_correct: int = Field(ge=0)
    candidate_only_correct: int = Field(ge=0)
    neither_correct: int = Field(ge=0)
    baseline_score: float = Field(ge=0, le=1)
    candidate_score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def counts_and_scores_must_be_consistent(self) -> PairedEvaluationReport:
        count_total = (
            self.both_correct
            + self.baseline_only_correct
            + self.candidate_only_correct
            + self.neither_correct
        )
        if count_total != self.total:
            raise ValueError("paired evaluation outcome counts must sum to total")
        expected_baseline = (self.both_correct + self.baseline_only_correct) / self.total
        expected_candidate = (self.both_correct + self.candidate_only_correct) / self.total
        if self.baseline_score != expected_baseline:
            raise ValueError("paired evaluation baseline score does not match outcome counts")
        if self.candidate_score != expected_candidate:
            raise ValueError("paired evaluation candidate score does not match outcome counts")
        if self.baseline_variant_id == self.candidate_variant_id:
            raise ValueError("paired evaluation variant ids must differ")
        if self.baseline_variant_sha256 == self.candidate_variant_sha256:
            raise ValueError("paired evaluation variants must have different identities")
        return self


class PromotionGateRule(StrictModel):
    """One exact statistical requirement for one evaluation partition."""

    role: PromotionEvidenceRole
    min_total: int = Field(default=30, ge=1, le=10_000)
    min_candidate_score: float = Field(default=0, ge=0, le=1)
    min_absolute_improvement: float = Field(default=0, ge=0, le=1)
    min_discordant_pairs: int = Field(default=1, ge=1, le=10_000)
    max_one_sided_p_value: float = Field(default=0.05, gt=0, le=1)


class PromotionGatePolicy(StrictModel):
    """Versioned promotion policy composed of independent partition rules."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    rules: list[PromotionGateRule] = Field(min_length=1, max_length=2)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value, noun="promotion policy")

    @model_validator(mode="after")
    def roles_must_be_unique(self) -> PromotionGatePolicy:
        roles = [rule.role for rule in self.rules]
        if len(roles) != len(set(roles)):
            raise ValueError("promotion policy roles must be unique")
        return self


class PromotionProposal(StrictModel):
    """Bind a candidate, baseline, and aggregate evaluation evidence."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    baseline_variant_id: str = Field(pattern=_ID_PATTERN)
    baseline_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_variant_id: str = Field(pattern=_ID_PATTERN)
    candidate_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence: list[PairedEvaluationReport] = Field(min_length=1, max_length=2)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value, noun="promotion proposal")

    @model_validator(mode="after")
    def proposal_must_be_unambiguous(self) -> PromotionProposal:
        if self.baseline_variant_id == self.candidate_variant_id:
            raise ValueError("promotion baseline and candidate ids must differ")
        if self.baseline_variant_sha256 == self.candidate_variant_sha256:
            raise ValueError("promotion baseline and candidate must have different identities")
        for report in self.evidence:
            if (
                report.baseline_variant_id != self.baseline_variant_id
                or report.baseline_variant_sha256 != self.baseline_variant_sha256
            ):
                raise ValueError("promotion evidence baseline must match proposal baseline")
            if (
                report.candidate_variant_id != self.candidate_variant_id
                or report.candidate_variant_sha256 != self.candidate_variant_sha256
            ):
                raise ValueError("promotion evidence candidate must match proposal candidate")
        roles = [report.role for report in self.evidence]
        if len(roles) != len(set(roles)):
            raise ValueError("promotion proposal evidence roles must be unique")
        evidence_ids = [report.evidence_id for report in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("promotion evidence ids must be unique")
        commitments = {
            (
                report.dataset_id,
                report.partition_id,
                report.public_dataset_sha256,
                report.public_partition_sha256,
            )
            for report in self.evidence
        }
        if len(commitments) != 1:
            raise ValueError("promotion evidence must share dataset and partition commitments")
        return self


class PromotionCheck(StrictModel):
    """One evaluated policy rule with stable statistics and reasons."""

    role: PromotionEvidenceRole
    evidence_id: str = Field(pattern=_ID_PATTERN)
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    passed: bool
    total: int = Field(ge=1)
    baseline_score: float = Field(ge=0, le=1)
    candidate_score: float = Field(ge=0, le=1)
    absolute_improvement: float = Field(ge=-1, le=1)
    discordant_pairs: int = Field(ge=0)
    candidate_only_correct: int = Field(ge=0)
    baseline_only_correct: int = Field(ge=0)
    one_sided_p_value: float = Field(ge=0, le=1)
    reasons: list[str]

    @model_validator(mode="after")
    def status_must_match_reasons(self) -> PromotionCheck:
        if self.passed == bool(self.reasons):
            raise ValueError("promotion check passed state must match its reasons")
        return self
