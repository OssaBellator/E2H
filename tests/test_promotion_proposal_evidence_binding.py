from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.promotion_models import (
    PairedEvaluationReport,
    PromotionError,
    PromotionEvidenceRole,
    PromotionProposal,
    promotion_proposal_sha256,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _report(**overrides: object) -> PairedEvaluationReport:
    values: dict[str, object] = {
        "evidence_id": "validation-evidence",
        "role": PromotionEvidenceRole.VALIDATION,
        "dataset_id": "dataset",
        "partition_id": "partition",
        "public_dataset_sha256": SHA_C,
        "public_partition_sha256": SHA_D,
        "baseline_variant_id": "baseline",
        "baseline_variant_sha256": SHA_A,
        "candidate_variant_id": "candidate",
        "candidate_variant_sha256": SHA_B,
        "total": 10,
        "both_correct": 5,
        "baseline_only_correct": 1,
        "candidate_only_correct": 2,
        "neither_correct": 2,
        "baseline_score": 0.6,
        "candidate_score": 0.7,
    }
    values.update(overrides)
    return PairedEvaluationReport.model_validate(values)


def _proposal(report: PairedEvaluationReport) -> PromotionProposal:
    return PromotionProposal(
        id="proposal",
        baseline_variant_id="baseline",
        baseline_variant_sha256=SHA_A,
        candidate_variant_id="candidate",
        candidate_variant_sha256=SHA_B,
        evidence=[report],
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("baseline_variant_id", "other-baseline", "evidence baseline must match"),
        ("baseline_variant_sha256", SHA_C, "evidence baseline must match"),
        ("candidate_variant_id", "other-candidate", "evidence candidate must match"),
        ("candidate_variant_sha256", SHA_D, "evidence candidate must match"),
    ],
)
def test_proposal_rejects_evidence_for_other_variants(
    field: str,
    value: object,
    message: str,
) -> None:
    report = _report(**{field: value})

    with pytest.raises(ValidationError, match=message):
        _proposal(report)


def test_proposal_accepts_evidence_for_declared_variants() -> None:
    proposal = _proposal(_report())

    assert proposal.evidence[0].baseline_variant_id == proposal.baseline_variant_id
    assert proposal.evidence[0].candidate_variant_id == proposal.candidate_variant_id


def test_proposal_digest_rejects_post_validation_evidence_mutation() -> None:
    proposal = _proposal(_report())
    proposal.evidence[0].baseline_variant_id = "other-baseline"

    with pytest.raises(PromotionError, match="evidence baseline must match proposal baseline"):
        promotion_proposal_sha256(proposal)
