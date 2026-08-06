from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from e2h.promotion import (
    PairedEvaluationReport,
    PromotionDecision,
    PromotionDecisionKind,
    PromotionError,
    PromotionGatePolicy,
    PromotionProposal,
    RollbackPlan,
    evaluate_promotion,
    materialize_promotion,
    promotion_decision_sha256,
    record_rollback,
)

BASE_SHA = "1" * 64
CANDIDATE_SHA = "2" * 64
DATASET_SHA = "3" * 64
PARTITION_SHA = "4" * 64


def _evidence(role: str = "validation") -> PairedEvaluationReport:
    return PairedEvaluationReport(
        evidence_id=f"{role}-evidence",
        role=role,
        dataset_id="integrity-dataset",
        partition_id="integrity-partition",
        public_dataset_sha256=DATASET_SHA,
        public_partition_sha256=PARTITION_SHA,
        baseline_variant_id="baseline",
        baseline_variant_sha256=BASE_SHA,
        candidate_variant_id="candidate",
        candidate_variant_sha256=CANDIDATE_SHA,
        total=10,
        both_correct=8,
        baseline_only_correct=0,
        candidate_only_correct=1,
        neither_correct=1,
        baseline_score=0.8,
        candidate_score=0.9,
    )


def _policy(*, include_sealed: bool = False) -> PromotionGatePolicy:
    rules = [
        {
            "role": "validation",
            "min_total": 10,
            "min_candidate_score": 0.9,
            "min_absolute_improvement": 0.1,
            "min_discordant_pairs": 1,
            "max_one_sided_p_value": 0.5,
        }
    ]
    if include_sealed:
        rules.append({**rules[0], "role": "sealed_test"})
    return PromotionGatePolicy(id="integrity-policy", rules=rules)


def _proposal(*, include_sealed: bool = False) -> PromotionProposal:
    evidence = [_evidence()]
    if include_sealed:
        evidence.append(_evidence("sealed_test"))
    return PromotionProposal(
        id="integrity-proposal",
        baseline_variant_id="baseline",
        baseline_variant_sha256=BASE_SHA,
        candidate_variant_id="candidate",
        candidate_variant_sha256=CANDIDATE_SHA,
        evidence=evidence,
    )


def _rollback(decision: PromotionDecision) -> RollbackPlan:
    return RollbackPlan(
        id="integrity-rollback",
        promotion_decision_sha256=promotion_decision_sha256(decision),
        active_variant_id="candidate",
        active_variant_sha256=CANDIDATE_SHA,
        target_variant_id="baseline",
        target_variant_sha256=BASE_SHA,
        triggers=[
            {
                "id": "pass-rate",
                "metric": "pass_rate",
                "operator": "lt",
                "threshold": 0.95,
                "min_samples": 20,
                "window_seconds": 3600,
            }
        ],
    )


def test_scores_must_use_the_canonical_value_implied_by_counts() -> None:
    payload = _evidence().model_dump(mode="json")
    payload["candidate_score"] += 1e-12
    with pytest.raises(ValidationError, match="candidate score"):
        PairedEvaluationReport.model_validate(payload)


def test_proposal_evidence_must_share_public_commitments() -> None:
    payload = _proposal(include_sealed=True).model_dump(mode="json")
    payload["evidence"][1]["public_dataset_sha256"] = "5" * 64
    with pytest.raises(ValidationError, match="share dataset and partition commitments"):
        PromotionProposal.model_validate(payload)


def test_exact_threshold_boundaries_promote() -> None:
    decision = evaluate_promotion(_policy(), _proposal())
    assert decision.decision is PromotionDecisionKind.PROMOTE
    assert decision.checks[0].absolute_improvement == pytest.approx(0.1)
    assert decision.checks[0].one_sided_p_value == 0.5


def test_decision_round_trip_recomputes_embedded_source_chain() -> None:
    decision = evaluate_promotion(_policy(), _proposal())
    assert PromotionDecision.model_validate_json(decision.model_dump_json()) == decision

    payload = decision.model_dump(mode="json")
    payload["checks"][0]["candidate_score"] = 0.5
    with pytest.raises(ValidationError, match="checks do not match"):
        PromotionDecision.model_validate(payload)

    payload = decision.model_dump(mode="json")
    payload["policy_sha256"] = "5" * 64
    with pytest.raises(ValidationError, match="policy digest"):
        PromotionDecision.model_validate(payload)


def test_receipt_preserves_policy_and_proposal_digests() -> None:
    decision = evaluate_promotion(_policy(), _proposal())
    receipt = materialize_promotion(decision, _rollback(decision))
    assert receipt.decision == decision
    assert receipt.policy_sha256 == decision.policy_sha256
    assert receipt.proposal_sha256 == decision.proposal_sha256

    payload = receipt.model_dump(mode="json")
    payload["policy_sha256"] = "5" * 64
    with pytest.raises(ValidationError, match="policy does not match"):
        type(receipt).model_validate(payload)


def test_rollback_observation_window_is_recorded_and_verified() -> None:
    decision = evaluate_promotion(_policy(), _proposal())
    receipt = materialize_promotion(decision, _rollback(decision))
    event = record_rollback(
        "rollback-event",
        receipt,
        "pass-rate",
        0.9,
        20,
        "controller",
        datetime(2026, 8, 7, tzinfo=UTC),
    )
    assert event.observed_window_seconds == 3600

    with pytest.raises(PromotionError, match="window does not match"):
        record_rollback(
            "wrong-window",
            receipt,
            "pass-rate",
            0.9,
            20,
            "controller",
            datetime(2026, 8, 7, tzinfo=UTC),
            observed_window_seconds=60,
        )
