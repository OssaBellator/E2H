"""Policy evaluation and materialization for promotion artifacts."""

from __future__ import annotations

from fractions import Fraction

from e2h.promotion_models import (
    PromotionCheck,
    PromotionDecision,
    PromotionDecisionKind,
    PromotionError,
    PromotionGatePolicy,
    PromotionProposal,
    PromotionReceipt,
    RollbackPlan,
    _exact_one_sided_mcnemar_tail,
    paired_evaluation_sha256,
    promotion_decision_sha256,
    promotion_policy_sha256,
    promotion_proposal_sha256,
    rollback_plan_sha256,
)
from e2h.promotion_validation import revalidate_promotion_model


def _promotion_checks(
    policy: PromotionGatePolicy,
    proposal: PromotionProposal,
) -> list[PromotionCheck]:
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

        candidate_score_fraction = Fraction(
            report.both_correct + report.candidate_only_correct,
            report.total,
        )
        improvement_fraction = Fraction(
            report.candidate_only_correct - report.baseline_only_correct,
            report.total,
        )
        improvement = float(improvement_fraction)
        discordant = report.candidate_only_correct + report.baseline_only_correct
        p_value_fraction = _exact_one_sided_mcnemar_tail(
            report.candidate_only_correct,
            report.baseline_only_correct,
        )
        p_value = float(p_value_fraction)
        reasons: list[str] = []
        if report.total < rule.min_total:
            reasons.append(f"total {report.total} is below required {rule.min_total}")
        if improvement_fraction <= 0:
            reasons.append("candidate must outperform baseline")
        if candidate_score_fraction < Fraction(str(rule.min_candidate_score)):
            reasons.append(
                f"candidate score {report.candidate_score:.6f} is below required "
                f"{rule.min_candidate_score:.6f}"
            )
        if improvement_fraction < Fraction(str(rule.min_absolute_improvement)):
            reasons.append(
                f"absolute improvement {improvement:.6f} is below required "
                f"{rule.min_absolute_improvement:.6f}"
            )
        if discordant < rule.min_discordant_pairs:
            reasons.append(
                f"discordant pairs {discordant} are below required {rule.min_discordant_pairs}"
            )
        if p_value_fraction > Fraction(str(rule.max_one_sided_p_value)):
            reasons.append(
                f"one-sided p-value {p_value:.6g} exceeds allowed {rule.max_one_sided_p_value:.6g}"
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
    return checks


def evaluate_promotion(
    policy: PromotionGatePolicy,
    proposal: PromotionProposal,
) -> PromotionDecision:
    """Evaluate all exact statistical rules and return a self-verifying decision."""
    try:
        policy = revalidate_promotion_model(
            policy,
            PromotionGatePolicy,
            noun="promotion policy",
        )
        proposal = revalidate_promotion_model(
            proposal,
            PromotionProposal,
            noun="promotion proposal",
        )
    except ValueError as exc:
        raise PromotionError(f"invalid promotion inputs: {exc}") from exc

    checks = _promotion_checks(policy, proposal)
    decision = (
        PromotionDecisionKind.PROMOTE
        if all(check.passed for check in checks)
        else PromotionDecisionKind.REJECT
    )
    return PromotionDecision(
        policy=policy,
        proposal=proposal,
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
    """Materialize a passing, internally verified decision with exact rollback metadata."""
    try:
        decision = revalidate_promotion_model(
            decision,
            PromotionDecision,
            noun="promotion decision",
        )
        rollback = revalidate_promotion_model(
            rollback,
            RollbackPlan,
            noun="rollback plan",
        )
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
        decision=decision,
        policy_id=decision.policy_id,
        policy_sha256=decision.policy_sha256,
        proposal_id=decision.proposal_id,
        proposal_sha256=decision.proposal_sha256,
        active_variant_id=decision.candidate_variant_id,
        active_variant_sha256=decision.candidate_variant_sha256,
        previous_variant_id=decision.baseline_variant_id,
        previous_variant_sha256=decision.baseline_variant_sha256,
        rollback_plan_sha256=rollback_plan_sha256(rollback),
        rollback=rollback,
    )
