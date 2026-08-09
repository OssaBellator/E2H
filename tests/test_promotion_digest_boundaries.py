from __future__ import annotations

import pytest

from e2h.promotion import (
    PairedEvaluationReport,
    PromotionDecision,
    PromotionError,
    PromotionGatePolicy,
    PromotionProposal,
    PromotionReceipt,
    RollbackPlan,
    VariantPredictionDocument,
    evaluate_promotion,
    materialize_promotion,
    paired_evaluation_sha256,
    promotion_decision_sha256,
    promotion_policy_sha256,
    promotion_proposal_sha256,
    promotion_receipt_sha256,
    rollback_plan_sha256,
    variant_prediction_sha256,
)

BASE_SHA = "1" * 64
CANDIDATE_SHA = "2" * 64
DATASET_SHA = "3" * 64
PARTITION_SHA = "4" * 64


def _prediction() -> VariantPredictionDocument:
    return VariantPredictionDocument.model_validate(
        {
            "variant_id": "baseline",
            "variant_sha256": BASE_SHA,
            "public_dataset_sha256": DATASET_SHA,
            "public_partition_sha256": PARTITION_SHA,
            "role": "validation",
            "predictions": [{"example_id": "case", "outputs": {"answer": "baseline"}}],
        }
    )


def _evidence() -> PairedEvaluationReport:
    return PairedEvaluationReport(
        evidence_id="validation-evidence",
        role="validation",
        dataset_id="dataset",
        partition_id="partition",
        public_dataset_sha256=DATASET_SHA,
        public_partition_sha256=PARTITION_SHA,
        baseline_variant_id="baseline",
        baseline_variant_sha256=BASE_SHA,
        candidate_variant_id="candidate",
        candidate_variant_sha256=CANDIDATE_SHA,
        total=1,
        both_correct=0,
        baseline_only_correct=0,
        candidate_only_correct=1,
        neither_correct=0,
        baseline_score=0,
        candidate_score=1,
    )


def _policy() -> PromotionGatePolicy:
    return PromotionGatePolicy(
        id="gate",
        rules=[
            {
                "role": "validation",
                "min_total": 1,
                "min_candidate_score": 1,
                "min_absolute_improvement": 1,
                "min_discordant_pairs": 1,
                "max_one_sided_p_value": 1,
            }
        ],
    )


def _proposal() -> PromotionProposal:
    return PromotionProposal(
        id="proposal",
        baseline_variant_id="baseline",
        baseline_variant_sha256=BASE_SHA,
        candidate_variant_id="candidate",
        candidate_variant_sha256=CANDIDATE_SHA,
        evidence=[_evidence()],
    )


def _decision() -> PromotionDecision:
    return evaluate_promotion(_policy(), _proposal())


def _plan(decision: PromotionDecision | None = None) -> RollbackPlan:
    decision = decision or _decision()
    return RollbackPlan(
        id="rollback",
        promotion_decision_sha256=promotion_decision_sha256(decision),
        active_variant_id="candidate",
        active_variant_sha256=CANDIDATE_SHA,
        target_variant_id="baseline",
        target_variant_sha256=BASE_SHA,
        triggers=[
            {
                "id": "error-rate",
                "metric": "error_rate",
                "operator": "gt",
                "threshold": 0.1,
            }
        ],
    )


def _receipt() -> PromotionReceipt:
    decision = _decision()
    return materialize_promotion(decision, _plan(decision))


def test_evidence_digest_boundaries_revalidate_mutations() -> None:
    prediction = _prediction()
    prediction.predictions.append(prediction.predictions[0])
    with pytest.raises(PromotionError, match="invalid variant prediction document"):
        variant_prediction_sha256(prediction)

    evidence = _evidence()
    evidence.total = 2
    with pytest.raises(PromotionError, match="invalid paired evaluation report"):
        paired_evaluation_sha256(evidence)


def test_policy_and_proposal_digest_boundaries_revalidate_mutations() -> None:
    policy = _policy()
    policy.rules.append(policy.rules[0])
    with pytest.raises(PromotionError, match="invalid promotion policy"):
        promotion_policy_sha256(policy)

    proposal = _proposal()
    proposal.evidence.append(proposal.evidence[0])
    with pytest.raises(PromotionError, match="invalid promotion proposal"):
        promotion_proposal_sha256(proposal)


def test_lifecycle_digest_boundaries_revalidate_mutations() -> None:
    decision = _decision()
    decision.checks.append(decision.checks[0])
    with pytest.raises(PromotionError, match="invalid promotion decision"):
        promotion_decision_sha256(decision)

    plan = _plan()
    plan.triggers.append(plan.triggers[0])
    with pytest.raises(PromotionError, match="invalid rollback plan"):
        rollback_plan_sha256(plan)

    receipt = _receipt()
    receipt.rollback.triggers.append(receipt.rollback.triggers[0])
    with pytest.raises(PromotionError, match="invalid promotion receipt"):
        promotion_receipt_sha256(receipt)
