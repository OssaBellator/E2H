"""Statistical promotion gates and immutable rollback metadata."""

from __future__ import annotations

import hashlib
import json
import math
import operator
import re
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import load_mapping_document
from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    PartitionRole,
    dataset_partition_public_sha256,
    dspy_dataset_public_sha256,
    verify_dataset_partitions,
)

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


_ModelT = TypeVar("_ModelT", bound=StrictModel)


def _canonical_json_bytes(value: Any) -> bytes:
    try:
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
        if not math.isclose(self.baseline_score, expected_baseline):
            raise ValueError("paired evaluation baseline score does not match outcome counts")
        if not math.isclose(self.candidate_score, expected_candidate):
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
        roles = [report.role for report in self.evidence]
        if len(roles) != len(set(roles)):
            raise ValueError("promotion proposal evidence roles must be unique")
        evidence_ids = [report.evidence_id for report in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("promotion evidence ids must be unique")
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


class PromotionDecisionKind(StrEnum):
    """Stable promotion outcomes."""

    PROMOTE = "promote"
    REJECT = "reject"


class PromotionDecision(StrictModel):
    """Deterministic decision bound to exact policy, proposal, and evidence."""

    schema_version: Literal["0.1"] = "0.1"
    policy_id: str = Field(pattern=_ID_PATTERN)
    policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    proposal_id: str = Field(pattern=_ID_PATTERN)
    proposal_sha256: str = Field(pattern=_SHA256_PATTERN)
    baseline_variant_id: str = Field(pattern=_ID_PATTERN)
    baseline_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_variant_id: str = Field(pattern=_ID_PATTERN)
    candidate_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    decision: PromotionDecisionKind
    checks: list[PromotionCheck] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def decision_must_match_checks(self) -> PromotionDecision:
        should_promote = all(check.passed for check in self.checks)
        if (self.decision is PromotionDecisionKind.PROMOTE) != should_promote:
            raise ValueError("promotion decision must match all policy checks")
        return self


class RollbackOperator(StrEnum):
    """Supported deterministic rollback comparisons."""

    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"


class RollbackTrigger(StrictModel):
    """One observable metric threshold that can activate rollback."""

    id: str = Field(pattern=_ID_PATTERN)
    metric: str = Field(pattern=_ID_PATTERN)
    operator: RollbackOperator
    threshold: float
    min_samples: int = Field(default=1, ge=1)
    window_seconds: int = Field(default=300, ge=1, le=31_536_000)

    @field_validator("threshold")
    @classmethod
    def threshold_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("rollback trigger threshold must be finite")
        return value


class RollbackPlan(StrictModel):
    """Immutable rollback target and observable trigger metadata."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    promotion_decision_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_variant_id: str = Field(pattern=_ID_PATTERN)
    active_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    target_variant_id: str = Field(pattern=_ID_PATTERN)
    target_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    target_locator: str | None = None
    triggers: list[RollbackTrigger] = Field(min_length=1, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_locator")
    @classmethod
    def locator_must_be_safe(cls, value: str | None) -> str | None:
        return _validate_locator(value)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value, noun="rollback plan")

    @model_validator(mode="after")
    def plan_must_be_unambiguous(self) -> RollbackPlan:
        trigger_ids = [trigger.id for trigger in self.triggers]
        if len(trigger_ids) != len(set(trigger_ids)):
            raise ValueError("rollback trigger ids must be unique")
        if self.active_variant_id == self.target_variant_id:
            raise ValueError("rollback active and target variant ids must differ")
        if self.active_variant_sha256 == self.target_variant_sha256:
            raise ValueError("rollback active and target variants must differ")
        return self


class PromotionReceipt(StrictModel):
    """Materialized promotion with an embedded immutable rollback plan."""

    schema_version: Literal["0.1"] = "0.1"
    promotion_decision_sha256: str = Field(pattern=_SHA256_PATTERN)
    policy_id: str = Field(pattern=_ID_PATTERN)
    proposal_id: str = Field(pattern=_ID_PATTERN)
    active_variant_id: str = Field(pattern=_ID_PATTERN)
    active_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    previous_variant_id: str = Field(pattern=_ID_PATTERN)
    previous_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    rollback_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    rollback: RollbackPlan

    @model_validator(mode="after")
    def embedded_plan_must_match(self) -> PromotionReceipt:
        if self.rollback_plan_sha256 != rollback_plan_sha256(self.rollback):
            raise ValueError("promotion receipt rollback digest does not match embedded plan")
        if self.rollback.promotion_decision_sha256 != self.promotion_decision_sha256:
            raise ValueError("promotion receipt rollback decision digest does not match")
        if (
            self.rollback.active_variant_id != self.active_variant_id
            or self.rollback.active_variant_sha256 != self.active_variant_sha256
        ):
            raise ValueError("promotion receipt rollback active variant does not match")
        if (
            self.rollback.target_variant_id != self.previous_variant_id
            or self.rollback.target_variant_sha256 != self.previous_variant_sha256
        ):
            raise ValueError("promotion receipt rollback target does not match previous variant")
        return self


class RollbackEvent(StrictModel):
    """Auditable evidence that one declared rollback trigger fired."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    promotion_receipt_sha256: str = Field(pattern=_SHA256_PATTERN)
    from_variant_id: str = Field(pattern=_ID_PATTERN)
    from_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    to_variant_id: str = Field(pattern=_ID_PATTERN)
    to_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    trigger_id: str = Field(pattern=_ID_PATTERN)
    observed_value: float
    observed_samples: int = Field(ge=1)
    actor: str = Field(pattern=_ID_PATTERN)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("rollback event occurred_at must be timezone-aware")
        return value

    @field_validator("observed_value")
    @classmethod
    def observed_value_must_be_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("rollback observed value must be finite")
        return value


def variant_prediction_sha256(document: VariantPredictionDocument) -> str:
    """Return the canonical identity of one variant prediction document."""
    return _model_sha256(document)


def paired_evaluation_sha256(report: PairedEvaluationReport) -> str:
    """Return the canonical identity of aggregate paired evidence."""
    return _model_sha256(report)


def promotion_policy_sha256(policy: PromotionGatePolicy) -> str:
    """Return the canonical identity of one promotion policy."""
    return _model_sha256(policy)


def promotion_proposal_sha256(proposal: PromotionProposal) -> str:
    """Return the canonical identity of one promotion proposal."""
    return _model_sha256(proposal)


def promotion_decision_sha256(decision: PromotionDecision) -> str:
    """Return the canonical identity of one promotion decision."""
    return _model_sha256(decision)


def rollback_plan_sha256(plan: RollbackPlan) -> str:
    """Return the canonical identity of one rollback plan."""
    return _model_sha256(plan)


def promotion_receipt_sha256(receipt: PromotionReceipt) -> str:
    """Return the canonical identity of one materialized promotion."""
    return _model_sha256(receipt)


def exact_one_sided_mcnemar_p_value(
    candidate_only_correct: int,
    baseline_only_correct: int,
) -> float:
    """Return P(X >= candidate wins) for X~Binomial(discordant, 0.5)."""
    if candidate_only_correct < 0 or baseline_only_correct < 0:
        raise ValueError("McNemar outcome counts must be non-negative")
    discordant = candidate_only_correct + baseline_only_correct
    if discordant == 0 or candidate_only_correct <= baseline_only_correct:
        return 1.0
    numerator = sum(
        math.comb(discordant, value)
        for value in range(candidate_only_correct, discordant + 1)
    )
    return float(Fraction(numerator, 1 << discordant))


def _validated_prediction_inputs(
    manifest: DatasetPartitionDocument,
    dataset: DSPyDatasetDocument,
    baseline: VariantPredictionDocument,
    candidate: VariantPredictionDocument,
) -> tuple[
    DatasetPartitionDocument,
    DSPyDatasetDocument,
    VariantPredictionDocument,
    VariantPredictionDocument,
]:
    try:
        return (
            DatasetPartitionDocument.model_validate(manifest.model_dump(mode="json")),
            DSPyDatasetDocument.model_validate(dataset.model_dump(mode="json")),
            VariantPredictionDocument.model_validate(baseline.model_dump(mode="json")),
            VariantPredictionDocument.model_validate(candidate.model_dump(mode="json")),
        )
    except ValueError as exc:
        raise PromotionError(f"invalid promotion comparison inputs: {exc}") from exc


def _prediction_map(
    document: VariantPredictionDocument,
    *,
    expected_ids: set[str],
    output_fields: set[str],
) -> dict[str, VariantPrediction]:
    predictions = {prediction.example_id: prediction for prediction in document.predictions}
    actual_ids = set(predictions)
    missing = sorted(expected_ids - actual_ids)
    unexpected = sorted(actual_ids - expected_ids)
    if missing:
        raise PromotionError(
            f"{document.variant_id} predictions are missing examples: " + ", ".join(missing)
        )
    if unexpected:
        raise PromotionError(
            f"{document.variant_id} predictions contain unexpected examples: "
            + ", ".join(unexpected)
        )
    for example_id, prediction in predictions.items():
        if set(prediction.outputs) != output_fields:
            raise PromotionError(
                f"{document.variant_id} prediction {example_id} must define exactly these outputs: "
                + ", ".join(sorted(output_fields))
            )
    return predictions


def compare_variant_predictions(
    evidence_id: str,
    manifest: DatasetPartitionDocument,
    dataset: DSPyDatasetDocument,
    baseline: VariantPredictionDocument,
    candidate: VariantPredictionDocument,
) -> PairedEvaluationReport:
    """Create aggregate paired correctness evidence without exposing labels."""
    if _ID_RE.fullmatch(evidence_id) is None:
        raise PromotionError("promotion evidence id must use a stable identifier")
    manifest, dataset, baseline, candidate = _validated_prediction_inputs(
        manifest,
        dataset,
        baseline,
        candidate,
    )
    verification = verify_dataset_partitions(manifest, dataset)
    if baseline.role is not candidate.role:
        raise PromotionError("baseline and candidate predictions must use the same partition role")
    if baseline.variant_sha256 == candidate.variant_sha256:
        raise PromotionError("baseline and candidate predictions must use different variants")
    for document in (baseline, candidate):
        if document.public_dataset_sha256 != verification.public_dataset_sha256:
            raise PromotionError(
                f"{document.variant_id} public dataset digest does not match the supplied dataset"
            )
        if document.public_partition_sha256 != verification.public_partition_sha256:
            raise PromotionError(
                f"{document.variant_id} public partition digest does not match "
                "the supplied manifest"
            )

    role = baseline.role
    example_ids = manifest.ids_for(role.partition_role())
    expected_ids = set(example_ids)
    output_fields = set(verification.output_fields)
    baseline_predictions = _prediction_map(
        baseline,
        expected_ids=expected_ids,
        output_fields=output_fields,
    )
    candidate_predictions = _prediction_map(
        candidate,
        expected_ids=expected_ids,
        output_fields=output_fields,
    )
    examples = {example.id: example for example in dataset.examples}

    both_correct = 0
    baseline_only_correct = 0
    candidate_only_correct = 0
    neither_correct = 0
    for example_id in example_ids:
        expected = _canonical_json_bytes(examples[example_id].outputs)
        baseline_correct = _canonical_json_bytes(
            baseline_predictions[example_id].outputs
        ) == expected
        candidate_correct = _canonical_json_bytes(
            candidate_predictions[example_id].outputs
        ) == expected
        if baseline_correct and candidate_correct:
            both_correct += 1
        elif baseline_correct:
            baseline_only_correct += 1
        elif candidate_correct:
            candidate_only_correct += 1
        else:
            neither_correct += 1

    total = len(example_ids)
    return PairedEvaluationReport(
        evidence_id=evidence_id,
        role=role,
        dataset_id=dataset.id,
        partition_id=manifest.id,
        public_dataset_sha256=dspy_dataset_public_sha256(dataset),
        public_partition_sha256=dataset_partition_public_sha256(manifest),
        baseline_variant_id=baseline.variant_id,
        baseline_variant_sha256=baseline.variant_sha256,
        candidate_variant_id=candidate.variant_id,
        candidate_variant_sha256=candidate.variant_sha256,
        total=total,
        both_correct=both_correct,
        baseline_only_correct=baseline_only_correct,
        candidate_only_correct=candidate_only_correct,
        neither_correct=neither_correct,
        baseline_score=(both_correct + baseline_only_correct) / total,
        candidate_score=(both_correct + candidate_only_correct) / total,
    )


def evaluate_promotion(
    policy: PromotionGatePolicy,
    proposal: PromotionProposal,
) -> PromotionDecision:
    """Evaluate all exact statistical rules and return a deterministic decision."""
    try:
        policy = PromotionGatePolicy.model_validate(policy.model_dump(mode="json"))
        proposal = PromotionProposal.model_validate(proposal.model_dump(mode="json"))
    except ValueError as exc:
        raise PromotionError(f"invalid promotion inputs: {exc}") from exc

    evidence_by_role = {report.role: report for report in proposal.evidence}
    policy_roles = {rule.role for rule in policy.rules}
    evidence_roles = set(evidence_by_role)
    missing = sorted(role.value for role in policy_roles - evidence_roles)
    unexpected = sorted(role.value for role in evidence_roles - policy_roles)
    if missing:
        raise PromotionError("promotion proposal is missing evidence roles: " + ", ".join(missing))
    if unexpected:
        raise PromotionError(
            "promotion proposal contains undeclared evidence roles: " + ", ".join(unexpected)
        )

    checks: list[PromotionCheck] = []
    for rule in policy.rules:
        report = evidence_by_role[rule.role]
        if (
            report.baseline_variant_id != proposal.baseline_variant_id
            or report.baseline_variant_sha256 != proposal.baseline_variant_sha256
        ):
            raise PromotionError(
                f"{rule.role.value} evidence baseline does not match the promotion proposal"
            )
        if (
            report.candidate_variant_id != proposal.candidate_variant_id
            or report.candidate_variant_sha256 != proposal.candidate_variant_sha256
        ):
            raise PromotionError(
                f"{rule.role.value} evidence candidate does not match the promotion proposal"
            )

        improvement = report.candidate_score - report.baseline_score
        discordant = report.candidate_only_correct + report.baseline_only_correct
        p_value = exact_one_sided_mcnemar_p_value(
            report.candidate_only_correct,
            report.baseline_only_correct,
        )
        reasons: list[str] = []
        if report.total < rule.min_total:
            reasons.append(f"total {report.total} is below required {rule.min_total}")
        if report.candidate_score < rule.min_candidate_score:
            reasons.append(
                f"candidate score {report.candidate_score:.6f} is below required "
                f"{rule.min_candidate_score:.6f}"
            )
        if improvement < rule.min_absolute_improvement:
            reasons.append(
                f"absolute improvement {improvement:.6f} is below required "
                f"{rule.min_absolute_improvement:.6f}"
            )
        if discordant < rule.min_discordant_pairs:
            reasons.append(
                f"discordant pairs {discordant} are below required {rule.min_discordant_pairs}"
            )
        if p_value > rule.max_one_sided_p_value:
            reasons.append(
                f"one-sided p-value {p_value:.6g} exceeds allowed "
                f"{rule.max_one_sided_p_value:.6g}"
            )
        checks.append(
            PromotionCheck(
                role=rule.role,
                evidence_id=report.evidence_id,
                evidence_sha256=paired_evaluation_sha256(report),
                passed=not reasons,
                total=report.total,
                baseline_score=report.baseline_score,
                candidate_score=report.candidate_score,
                absolute_improvement=improvement,
                discordant_pairs=discordant,
                candidate_only_correct=report.candidate_only_correct,
                baseline_only_correct=report.baseline_only_correct,
                one_sided_p_value=p_value,
                reasons=reasons,
            )
        )

    decision = (
        PromotionDecisionKind.PROMOTE
        if all(check.passed for check in checks)
        else PromotionDecisionKind.REJECT
    )
    return PromotionDecision(
        policy_id=policy.id,
        policy_sha256=promotion_policy_sha256(policy),
        proposal_id=proposal.id,
        proposal_sha256=promotion_proposal_sha256(proposal),
        baseline_variant_id=proposal.baseline_variant_id,
        baseline_variant_sha256=proposal.baseline_variant_sha256,
        candidate_variant_id=proposal.candidate_variant_id,
        candidate_variant_sha256=proposal.candidate_variant_sha256,
        decision=decision,
        checks=checks,
    )


def materialize_promotion(
    decision: PromotionDecision,
    rollback: RollbackPlan,
) -> PromotionReceipt:
    """Materialize a passing decision only when rollback targets the exact baseline."""
    try:
        decision = PromotionDecision.model_validate(decision.model_dump(mode="json"))
        rollback = RollbackPlan.model_validate(rollback.model_dump(mode="json"))
    except ValueError as exc:
        raise PromotionError(f"invalid promotion materialization inputs: {exc}") from exc
    if decision.decision is not PromotionDecisionKind.PROMOTE:
        raise PromotionError("only a passing promotion decision can be materialized")
    digest = promotion_decision_sha256(decision)
    if rollback.promotion_decision_sha256 != digest:
        raise PromotionError("rollback plan decision digest does not match the promotion decision")
    if (
        rollback.active_variant_id != decision.candidate_variant_id
        or rollback.active_variant_sha256 != decision.candidate_variant_sha256
    ):
        raise PromotionError("rollback plan active variant does not match the promoted candidate")
    if (
        rollback.target_variant_id != decision.baseline_variant_id
        or rollback.target_variant_sha256 != decision.baseline_variant_sha256
    ):
        raise PromotionError("rollback plan target does not match the promotion baseline")
    return PromotionReceipt(
        promotion_decision_sha256=digest,
        policy_id=decision.policy_id,
        proposal_id=decision.proposal_id,
        active_variant_id=decision.candidate_variant_id,
        active_variant_sha256=decision.candidate_variant_sha256,
        previous_variant_id=decision.baseline_variant_id,
        previous_variant_sha256=decision.baseline_variant_sha256,
        rollback_plan_sha256=rollback_plan_sha256(rollback),
        rollback=rollback,
    )


_ROLLBACK_OPERATORS: dict[
    RollbackOperator,
    Callable[[float, float], bool],
] = {
    RollbackOperator.LT: operator.lt,
    RollbackOperator.LTE: operator.le,
    RollbackOperator.GT: operator.gt,
    RollbackOperator.GTE: operator.ge,
}


def rollback_triggered(
    trigger: RollbackTrigger,
    observed_value: float,
    observed_samples: int,
) -> bool:
    """Return whether one finite observation satisfies a declared rollback trigger."""
    if not math.isfinite(observed_value):
        raise PromotionError("rollback observed value must be finite")
    if observed_samples < 1:
        raise PromotionError("rollback observed samples must be positive")
    if observed_samples < trigger.min_samples:
        return False
    return _ROLLBACK_OPERATORS[trigger.operator](observed_value, trigger.threshold)


def record_rollback(
    event_id: str,
    receipt: PromotionReceipt,
    trigger_id: str,
    observed_value: float,
    observed_samples: int,
    actor: str,
    occurred_at: datetime,
) -> RollbackEvent:
    """Record a rollback only when an embedded trigger actually fires."""
    if _ID_RE.fullmatch(event_id) is None or _ID_RE.fullmatch(actor) is None:
        raise PromotionError("rollback event id and actor must use stable identifiers")
    try:
        receipt = PromotionReceipt.model_validate(receipt.model_dump(mode="json"))
    except ValueError as exc:
        raise PromotionError(f"invalid promotion receipt: {exc}") from exc
    triggers = {trigger.id: trigger for trigger in receipt.rollback.triggers}
    trigger = triggers.get(trigger_id)
    if trigger is None:
        raise PromotionError(f"rollback trigger is not declared: {trigger_id}")
    if not rollback_triggered(trigger, observed_value, observed_samples):
        raise PromotionError("rollback observation does not satisfy the declared trigger")
    return RollbackEvent(
        id=event_id,
        promotion_receipt_sha256=promotion_receipt_sha256(receipt),
        from_variant_id=receipt.active_variant_id,
        from_variant_sha256=receipt.active_variant_sha256,
        to_variant_id=receipt.previous_variant_id,
        to_variant_sha256=receipt.previous_variant_sha256,
        trigger_id=trigger_id,
        observed_value=observed_value,
        observed_samples=observed_samples,
        actor=actor,
        occurred_at=occurred_at,
    )


def _load_model(path: Path, model: type[_ModelT], *, noun: str) -> _ModelT:
    try:
        payload = load_mapping_document(path, noun=noun, max_bytes=_MAX_DOCUMENT_BYTES)
        return model.model_validate(payload)
    except ValueError as exc:
        raise PromotionError(str(exc)) from exc


def load_variant_predictions(path: Path) -> VariantPredictionDocument:
    """Load one strict JSON or YAML variant prediction document."""
    return _load_model(path, VariantPredictionDocument, noun="variant prediction document")


def load_paired_evaluation(path: Path) -> PairedEvaluationReport:
    """Load one strict aggregate paired evaluation report."""
    return _load_model(path, PairedEvaluationReport, noun="paired evaluation report")


def load_promotion_policy(path: Path) -> PromotionGatePolicy:
    """Load one strict JSON or YAML promotion policy."""
    return _load_model(path, PromotionGatePolicy, noun="promotion gate policy")


def load_promotion_proposal(path: Path) -> PromotionProposal:
    """Load one strict JSON or YAML promotion proposal."""
    return _load_model(path, PromotionProposal, noun="promotion proposal")


def load_promotion_decision(path: Path) -> PromotionDecision:
    """Load one strict JSON or YAML promotion decision."""
    return _load_model(path, PromotionDecision, noun="promotion decision")


def load_rollback_plan(path: Path) -> RollbackPlan:
    """Load one strict JSON or YAML rollback plan."""
    return _load_model(path, RollbackPlan, noun="rollback plan")


def load_promotion_receipt(path: Path) -> PromotionReceipt:
    """Load one strict JSON or YAML promotion receipt."""
    return _load_model(path, PromotionReceipt, noun="promotion receipt")
