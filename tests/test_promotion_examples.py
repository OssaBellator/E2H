from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from e2h import (
    PromotionDecisionKind,
    compare_variant_predictions,
    evaluate_promotion,
    load_dataset_partitions,
    load_dspy_dataset,
    load_promotion_policy,
    load_promotion_proposal,
    load_rollback_plan,
    load_variant_predictions,
    materialize_promotion,
    promotion_decision_sha256,
    promotion_receipt_sha256,
    record_rollback,
)


def test_committed_promotion_examples_form_digest_bound_chain() -> None:
    root = Path(__file__).parents[1]
    directory = root / "examples/optimizer"
    dataset = load_dspy_dataset(directory / "dataset.yaml")
    manifest = load_dataset_partitions(directory / "partition.yaml")

    validation = compare_variant_predictions(
        "optimizer-validation",
        manifest,
        dataset,
        load_variant_predictions(directory / "promotion-baseline-validation.yaml"),
        load_variant_predictions(directory / "promotion-candidate-validation.yaml"),
    )
    sealed = compare_variant_predictions(
        "optimizer-sealed",
        manifest,
        dataset,
        load_variant_predictions(directory / "promotion-baseline-sealed.yaml"),
        load_variant_predictions(directory / "promotion-candidate-sealed.yaml"),
    )
    proposal = load_promotion_proposal(directory / "promotion-proposal.yaml")
    assert proposal.evidence == [validation, sealed]

    decision = evaluate_promotion(
        load_promotion_policy(directory / "promotion-policy.yaml"),
        proposal,
    )
    assert decision.decision is PromotionDecisionKind.PROMOTE
    assert all(check.passed for check in decision.checks)

    rollback = load_rollback_plan(directory / "promotion-rollback.yaml")
    assert rollback.promotion_decision_sha256 == promotion_decision_sha256(decision)
    receipt = materialize_promotion(decision, rollback)
    assert receipt.active_variant_id == "typed-candidate-gepa"
    assert receipt.previous_variant_id == "typed-candidate"

    event = record_rollback(
        "optimizer-example-event",
        receipt,
        "pass-rate-regression",
        0.90,
        20,
        "deployment-controller",
        datetime(2026, 8, 7, tzinfo=UTC),
    )
    assert event.promotion_receipt_sha256 == promotion_receipt_sha256(receipt)
    assert event.from_variant_id == "typed-candidate-gepa"
    assert event.to_variant_id == "typed-candidate"

    aggregate_json = validation.model_dump_json() + sealed.model_dump_json()
    assert "expected_status" not in aggregate_json
    assert "correction-task" not in aggregate_json
    assert "sealed-task" not in aggregate_json
    assert '"passed"' not in aggregate_json
    assert '"failed"' not in aggregate_json
