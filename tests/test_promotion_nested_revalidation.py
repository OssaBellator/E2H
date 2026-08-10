from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.promotion_models import (
    PairedEvaluationReport,
    PromotionEvidenceRole,
    PromotionGatePolicy,
    PromotionGateRule,
    PromotionProposal,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _report() -> PairedEvaluationReport:
    return PairedEvaluationReport(
        evidence_id="validation-evidence",
        role=PromotionEvidenceRole.VALIDATION,
        dataset_id="dataset",
        partition_id="partition",
        public_dataset_sha256=SHA_C,
        public_partition_sha256=SHA_D,
        baseline_variant_id="baseline",
        baseline_variant_sha256=SHA_A,
        candidate_variant_id="candidate",
        candidate_variant_sha256=SHA_B,
        total=10,
        both_correct=5,
        baseline_only_correct=1,
        candidate_only_correct=2,
        neither_correct=2,
        baseline_score=0.6,
        candidate_score=0.7,
    )


def test_proposal_revalidates_mutated_evidence_instance() -> None:
    report = _report()
    report.evidence_id = "invalid evidence id"

    with pytest.raises(ValidationError) as exc_info:
        PromotionProposal(
            id="proposal",
            baseline_variant_id="baseline",
            baseline_variant_sha256=SHA_A,
            candidate_variant_id="candidate",
            candidate_variant_sha256=SHA_B,
            evidence=[report],
        )

    assert exc_info.value.errors()[0]["loc"] == ("evidence", 0, "evidence_id")


def test_policy_revalidates_mutated_rule_instance() -> None:
    rule = PromotionGateRule(role=PromotionEvidenceRole.VALIDATION)
    rule.min_total = 0

    with pytest.raises(ValidationError) as exc_info:
        PromotionGatePolicy(id="policy", rules=[rule])

    assert exc_info.value.errors()[0]["loc"] == ("rules", 0, "min_total")
