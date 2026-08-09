from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from e2h.promotion import (
    PairedEvaluationReport,
    PromotionError,
    PromotionGatePolicy,
    PromotionProposal,
    RollbackPlan,
    VariantPrediction,
    VariantPredictionDocument,
    promotion_policy_sha256,
    variant_prediction_sha256,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _evidence() -> PairedEvaluationReport:
    return PairedEvaluationReport(
        evidence_id="evidence",
        role="validation",
        dataset_id="dataset",
        partition_id="partition",
        public_dataset_sha256=SHA_A,
        public_partition_sha256=SHA_B,
        baseline_variant_id="baseline",
        baseline_variant_sha256=SHA_C,
        candidate_variant_id="candidate",
        candidate_variant_sha256=SHA_D,
        total=1,
        both_correct=0,
        baseline_only_correct=0,
        candidate_only_correct=1,
        neither_correct=0,
        baseline_score=0,
        candidate_score=1,
    )


def _policy(metadata: dict[str, Any] | None = None) -> PromotionGatePolicy:
    return PromotionGatePolicy(
        id="policy",
        rules=[{"role": "validation"}],
        metadata={} if metadata is None else metadata,
    )


@pytest.mark.parametrize(
    "outputs",
    [
        {"answer": {"nested": {1: "coerced key"}}},
        {"answer": {"nested": (1, 2)}},
    ],
)
def test_variant_predictions_reject_json_coercible_outputs(outputs: Any) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        VariantPrediction(example_id="case", outputs=outputs)


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_promotion_metadata_rejects_json_coercible_values(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _policy(metadata)

    with pytest.raises(ValidationError, match="canonical JSON data"):
        PromotionProposal(
            id="proposal",
            baseline_variant_id="baseline",
            baseline_variant_sha256=SHA_C,
            candidate_variant_id="candidate",
            candidate_variant_sha256=SHA_D,
            evidence=[_evidence()],
            metadata=metadata,
        )

    with pytest.raises(ValidationError, match="canonical JSON data"):
        RollbackPlan(
            id="rollback",
            promotion_decision_sha256=SHA_A,
            active_variant_id="candidate",
            active_variant_sha256=SHA_D,
            target_variant_id="baseline",
            target_variant_sha256=SHA_C,
            triggers=[{"id": "errors", "metric": "error_rate", "operator": "gt", "threshold": 0.1}],
            metadata=metadata,
        )


def test_promotion_digest_boundaries_reject_mutated_json_coercion() -> None:
    prediction = VariantPredictionDocument(
        variant_id="candidate",
        variant_sha256=SHA_D,
        public_dataset_sha256=SHA_A,
        public_partition_sha256=SHA_B,
        role="validation",
        predictions=[VariantPrediction(example_id="case", outputs={"answer": "ok"})],
    )
    prediction.predictions[0].outputs["answer"] = {"nested": {1: "coerced key"}}

    with pytest.raises(PromotionError, match="invalid variant prediction document"):
        variant_prediction_sha256(prediction)

    policy = _policy()
    policy.metadata["nested"] = (1, 2)

    with pytest.raises(PromotionError, match="invalid promotion policy"):
        promotion_policy_sha256(policy)


def test_promotion_models_preserve_exact_nested_json() -> None:
    nested = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}
    policy = _policy(nested)
    prediction = VariantPrediction(example_id="case", outputs={"answer": nested})

    assert policy.metadata == nested
    assert prediction.outputs["answer"] == nested
