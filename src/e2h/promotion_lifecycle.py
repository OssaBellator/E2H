"""Decision, receipt, and rollback models for promotion artifacts."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from e2h.promotion_evidence import (
    _ID_PATTERN,
    _SHA256_PATTERN,
    PairedEvaluationReport,
    PromotionCheck,
    PromotionGatePolicy,
    PromotionProposal,
    StrictModel,
    VariantPredictionDocument,
    _model_sha256,
    _validate_locator,
    _validate_metadata,
)


class PromotionDecisionKind(StrEnum):
    """Stable promotion outcomes."""

    PROMOTE = "promote"
    REJECT = "reject"


class PromotionDecision(StrictModel):
    """Self-verifying decision bound to exact policy, proposal, and evidence."""

    schema_version: Literal["0.1"] = "0.1"
    policy: PromotionGatePolicy
    proposal: PromotionProposal
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
    def decision_must_verify_its_source_chain(self) -> PromotionDecision:
        if self.policy_id != self.policy.id:
            raise ValueError("promotion decision policy id does not match embedded policy")
        if self.policy_sha256 != promotion_policy_sha256(self.policy):
            raise ValueError("promotion decision policy digest does not match embedded policy")
        if self.proposal_id != self.proposal.id:
            raise ValueError("promotion decision proposal id does not match embedded proposal")
        if self.proposal_sha256 != promotion_proposal_sha256(self.proposal):
            raise ValueError("promotion decision proposal digest does not match embedded proposal")
        if (
            self.baseline_variant_id != self.proposal.baseline_variant_id
            or self.baseline_variant_sha256 != self.proposal.baseline_variant_sha256
        ):
            raise ValueError("promotion decision baseline does not match embedded proposal")
        if (
            self.candidate_variant_id != self.proposal.candidate_variant_id
            or self.candidate_variant_sha256 != self.proposal.candidate_variant_sha256
        ):
            raise ValueError("promotion decision candidate does not match embedded proposal")
        roles = [check.role for check in self.checks]
        if len(roles) != len(set(roles)):
            raise ValueError("promotion decision check roles must be unique")
        evidence_ids = [check.evidence_id for check in self.checks]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("promotion decision evidence ids must be unique")
        from e2h.promotion_evaluation import _promotion_checks

        expected_checks = _promotion_checks(self.policy, self.proposal)
        if self.checks != expected_checks:
            raise ValueError("promotion decision checks do not match embedded policy and proposal")
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
    decision: PromotionDecision
    policy_id: str = Field(pattern=_ID_PATTERN)
    policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    proposal_id: str = Field(pattern=_ID_PATTERN)
    proposal_sha256: str = Field(pattern=_SHA256_PATTERN)
    active_variant_id: str = Field(pattern=_ID_PATTERN)
    active_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    previous_variant_id: str = Field(pattern=_ID_PATTERN)
    previous_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    rollback_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    rollback: RollbackPlan

    @model_validator(mode="after")
    def embedded_artifacts_must_match(self) -> PromotionReceipt:
        if self.promotion_decision_sha256 != promotion_decision_sha256(self.decision):
            raise ValueError("promotion receipt decision digest does not match embedded decision")
        if (
            self.policy_id != self.decision.policy_id
            or self.policy_sha256 != self.decision.policy_sha256
        ):
            raise ValueError("promotion receipt policy does not match embedded decision")
        if (
            self.proposal_id != self.decision.proposal_id
            or self.proposal_sha256 != self.decision.proposal_sha256
        ):
            raise ValueError("promotion receipt proposal does not match embedded decision")
        if (
            self.active_variant_id != self.decision.candidate_variant_id
            or self.active_variant_sha256 != self.decision.candidate_variant_sha256
        ):
            raise ValueError("promotion receipt active variant does not match embedded decision")
        if (
            self.previous_variant_id != self.decision.baseline_variant_id
            or self.previous_variant_sha256 != self.decision.baseline_variant_sha256
        ):
            raise ValueError("promotion receipt previous variant does not match embedded decision")
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
    observed_window_seconds: int = Field(ge=1, le=31_536_000)
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


def _exact_one_sided_mcnemar_tail(
    candidate_only_correct: int,
    baseline_only_correct: int,
) -> Fraction:
    if candidate_only_correct < 0 or baseline_only_correct < 0:
        raise ValueError("McNemar outcome counts must be non-negative")
    discordant = candidate_only_correct + baseline_only_correct
    if discordant == 0 or candidate_only_correct <= baseline_only_correct:
        return Fraction(1, 1)
    numerator = sum(
        math.comb(discordant, value)
        for value in range(candidate_only_correct, discordant + 1)
    )
    return Fraction(numerator, 1 << discordant)


def exact_one_sided_mcnemar_p_value(
    candidate_only_correct: int,
    baseline_only_correct: int,
) -> float:
    """Return P(X >= candidate wins) for X~Binomial(discordant, 0.5)."""
    return float(
        _exact_one_sided_mcnemar_tail(
            candidate_only_correct,
            baseline_only_correct,
        )
    )
