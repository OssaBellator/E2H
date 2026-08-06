from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    dataset_partition_public_sha256,
    dspy_dataset_public_sha256,
    dspy_dataset_sha256,
)
from e2h.promotion import (
    PairedEvaluationReport,
    PromotionDecision,
    PromotionDecisionKind,
    PromotionError,
    PromotionGatePolicy,
    PromotionProposal,
    PromotionReceipt,
    RollbackOperator,
    RollbackPlan,
    RollbackTrigger,
    VariantPredictionDocument,
    compare_variant_predictions,
    evaluate_promotion,
    exact_one_sided_mcnemar_p_value,
    load_paired_evaluation,
    load_promotion_decision,
    load_promotion_policy,
    load_promotion_proposal,
    load_promotion_receipt,
    load_rollback_plan,
    load_variant_predictions,
    materialize_promotion,
    paired_evaluation_sha256,
    promotion_decision_sha256,
    promotion_policy_sha256,
    promotion_proposal_sha256,
    promotion_receipt_sha256,
    record_rollback,
    rollback_plan_sha256,
    rollback_triggered,
    variant_prediction_sha256,
)

BASE_SHA = "1" * 64
CANDIDATE_SHA = "2" * 64


def dataset() -> DSPyDatasetDocument:
    examples = [
        {"id": "train", "inputs": {"task": "train"}, "outputs": {"answer": "ok"}}
    ]
    for role in ("validation", "sealed"):
        for index in range(6):
            examples.append(
                {
                    "id": f"{role}-{index}",
                    "inputs": {"task": f"{role} {index}"},
                    "outputs": {"answer": "ok"},
                    "metadata": {"private_note": f"secret-{role}-{index}"},
                }
            )
    return DSPyDatasetDocument.model_validate(
        {
            "id": "promotion-suite",
            "examples": examples,
            "metadata": {"private": "secret"},
        }
    )


def manifest(source: DSPyDatasetDocument | None = None) -> DatasetPartitionDocument:
    source = source or dataset()
    return DatasetPartitionDocument(
        id="promotion-split",
        dataset_sha256=dspy_dataset_sha256(source),
        public_dataset_sha256=dspy_dataset_public_sha256(source),
        train=["train"],
        validation=[f"validation-{index}" for index in range(6)],
        sealed_test=[f"sealed-{index}" for index in range(6)],
    )


def predictions(
    split: DatasetPartitionDocument,
    *,
    role: str = "validation",
    candidate: bool = False,
) -> VariantPredictionDocument:
    prefix = "validation" if role == "validation" else "sealed"
    values = []
    for index in range(6):
        answer = "ok" if candidate and index < 5 else "wrong"
        values.append({"example_id": f"{prefix}-{index}", "outputs": {"answer": answer}})
    return VariantPredictionDocument.model_validate(
        {
            "variant_id": "candidate" if candidate else "baseline",
            "variant_sha256": CANDIDATE_SHA if candidate else BASE_SHA,
            "public_dataset_sha256": split.public_dataset_sha256,
            "public_partition_sha256": dataset_partition_public_sha256(split),
            "role": role,
            "predictions": values,
        }
    )


def report(role: str = "validation") -> PairedEvaluationReport:
    source = dataset()
    split = manifest(source)
    return compare_variant_predictions(
        f"{role}-evidence",
        split,
        source,
        predictions(split, role=role),
        predictions(split, role=role, candidate=True),
    )


def policy(*, include_sealed: bool = False) -> PromotionGatePolicy:
    rules = [
        {
            "role": "validation",
            "min_total": 6,
            "min_candidate_score": 0.8,
            "min_absolute_improvement": 0.8,
            "min_discordant_pairs": 5,
            "max_one_sided_p_value": 0.05,
        }
    ]
    if include_sealed:
        rules.append({**rules[0], "role": "sealed_test"})
    return PromotionGatePolicy(id="strict-gate", rules=rules)


def proposal(*, include_sealed: bool = False) -> PromotionProposal:
    evidence = [report()]
    if include_sealed:
        evidence.append(report("sealed_test"))
    return PromotionProposal(
        id="candidate-promotion",
        baseline_variant_id="baseline",
        baseline_variant_sha256=BASE_SHA,
        candidate_variant_id="candidate",
        candidate_variant_sha256=CANDIDATE_SHA,
        evidence=evidence,
    )


def passing_decision() -> PromotionDecision:
    return evaluate_promotion(policy(), proposal())


def rollback_plan(decision: PromotionDecision | None = None) -> RollbackPlan:
    decision = decision or passing_decision()
    return RollbackPlan(
        id="candidate-rollback",
        promotion_decision_sha256=promotion_decision_sha256(decision),
        active_variant_id="candidate",
        active_variant_sha256=CANDIDATE_SHA,
        target_variant_id="baseline",
        target_variant_sha256=BASE_SHA,
        target_locator="variant://baseline",
        triggers=[
            {
                "id": "error-rate",
                "metric": "error_rate",
                "operator": "gt",
                "threshold": 0.1,
                "min_samples": 20,
                "window_seconds": 600,
            }
        ],
    )


def receipt() -> PromotionReceipt:
    decision = passing_decision()
    return materialize_promotion(decision, rollback_plan(decision))


def test_compare_predictions_returns_aggregate_paired_evidence() -> None:
    evidence = report()

    assert evidence.total == 6
    assert evidence.both_correct == 0
    assert evidence.baseline_only_correct == 0
    assert evidence.candidate_only_correct == 5
    assert evidence.neither_correct == 1
    assert evidence.baseline_score == 0
    assert evidence.candidate_score == pytest.approx(5 / 6)
    rendered = evidence.model_dump_json()
    assert "validation-0" not in rendered
    assert '"ok"' not in rendered
    assert "private_note" not in rendered
    assert "secret" not in rendered


def test_compare_supports_validation_and_sealed_roles() -> None:
    validation = report("validation")
    sealed = report("sealed_test")
    assert validation.role.value == "validation"
    assert sealed.role.value == "sealed_test"
    assert sealed.public_dataset_sha256 == validation.public_dataset_sha256
    assert sealed.public_partition_sha256 == validation.public_partition_sha256


def test_compare_requires_same_role_and_distinct_variants() -> None:
    source = dataset()
    split = manifest(source)
    baseline = predictions(split)
    candidate = predictions(split, role="sealed_test", candidate=True)
    with pytest.raises(PromotionError, match="same partition role"):
        compare_variant_predictions("bad", split, source, baseline, candidate)

    same = predictions(split, candidate=True)
    same.variant_sha256 = baseline.variant_sha256
    with pytest.raises(PromotionError, match="different variants"):
        compare_variant_predictions("bad", split, source, baseline, same)


def test_compare_rejects_bad_commitments_membership_and_signature() -> None:
    source = dataset()
    split = manifest(source)
    baseline = predictions(split)
    candidate = predictions(split, candidate=True)

    baseline.public_dataset_sha256 = "3" * 64
    with pytest.raises(PromotionError, match="public dataset digest"):
        compare_variant_predictions("bad", split, source, baseline, candidate)

    baseline = predictions(split)
    baseline.public_partition_sha256 = "3" * 64
    with pytest.raises(PromotionError, match="public partition digest"):
        compare_variant_predictions("bad", split, source, baseline, candidate)

    baseline = predictions(split)
    baseline.predictions.pop()
    with pytest.raises(PromotionError, match="missing examples"):
        compare_variant_predictions("bad", split, source, baseline, candidate)

    baseline = predictions(split)
    baseline.predictions.append(
        baseline.predictions[0].model_copy(update={"example_id": "train"})
    )
    with pytest.raises(PromotionError, match="unexpected examples"):
        compare_variant_predictions("bad", split, source, baseline, candidate)

    baseline = predictions(split)
    baseline.predictions[0].outputs = {"other": "wrong"}
    with pytest.raises(PromotionError, match="exactly these outputs"):
        compare_variant_predictions("bad", split, source, baseline, candidate)


def test_compare_revalidates_mutated_models_and_evidence_id() -> None:
    source = dataset()
    split = manifest(source)
    baseline = predictions(split)
    baseline.predictions.append(baseline.predictions[0])
    with pytest.raises(PromotionError, match="unique"):
        compare_variant_predictions(
            "evidence",
            split,
            source,
            baseline,
            predictions(split, candidate=True),
        )

    with pytest.raises(PromotionError, match="stable identifier"):
        compare_variant_predictions(
            "bad id",
            split,
            source,
            predictions(split),
            predictions(split, candidate=True),
        )


def test_prediction_and_evidence_models_reject_invalid_content() -> None:
    split = manifest()
    payload = predictions(split).model_dump(mode="json")
    payload["predictions"][1]["example_id"] = payload["predictions"][0]["example_id"]
    with pytest.raises(ValidationError, match="unique"):
        VariantPredictionDocument.model_validate(payload)

    payload = predictions(split).model_dump(mode="json")
    payload["predictions"][0]["outputs"] = {"bad key": "x"}
    with pytest.raises(ValidationError, match="Python identifiers"):
        VariantPredictionDocument.model_validate(payload)

    payload = predictions(split).model_dump(mode="json")
    payload["predictions"][0]["outputs"] = {"answer": float("nan")}
    with pytest.raises(ValidationError, match="canonical JSON"):
        VariantPredictionDocument.model_validate(payload)

    evidence_payload = report().model_dump(mode="json")
    evidence_payload["total"] += 1
    with pytest.raises(ValidationError, match="sum to total"):
        PairedEvaluationReport.model_validate(evidence_payload)

    evidence_payload = report().model_dump(mode="json")
    evidence_payload["candidate_score"] = 0
    with pytest.raises(ValidationError, match="candidate score"):
        PairedEvaluationReport.model_validate(evidence_payload)


@pytest.mark.parametrize(
    ("candidate_only", "baseline_only", "expected"),
    [(5, 0, 0.03125), (3, 1, 0.3125), (0, 0, 1.0), (1, 2, 1.0)],
)
def test_exact_mcnemar_tail(candidate_only: int, baseline_only: int, expected: float) -> None:
    assert exact_one_sided_mcnemar_p_value(candidate_only, baseline_only) == expected


def test_exact_mcnemar_rejects_negative_counts() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        exact_one_sided_mcnemar_p_value(-1, 0)


def test_policy_and_proposal_reject_ambiguous_roles_and_variants() -> None:
    payload = policy().model_dump(mode="json")
    payload["rules"].append(payload["rules"][0])
    with pytest.raises(ValidationError, match="roles must be unique"):
        PromotionGatePolicy.model_validate(payload)

    payload = proposal().model_dump(mode="json")
    payload["candidate_variant_id"] = payload["baseline_variant_id"]
    with pytest.raises(ValidationError, match="ids must differ"):
        PromotionProposal.model_validate(payload)

    payload = proposal().model_dump(mode="json")
    payload["evidence"].append(payload["evidence"][0])
    with pytest.raises(ValidationError, match="evidence roles must be unique"):
        PromotionProposal.model_validate(payload)


def test_evaluate_promotion_passes_all_declared_roles() -> None:
    decision = evaluate_promotion(policy(include_sealed=True), proposal(include_sealed=True))

    assert decision.decision is PromotionDecisionKind.PROMOTE
    assert len(decision.checks) == 2
    assert all(check.passed for check in decision.checks)
    assert all(check.one_sided_p_value == 0.03125 for check in decision.checks)
    assert decision.policy_sha256 == promotion_policy_sha256(policy(include_sealed=True))
    assert decision.proposal_sha256 == promotion_proposal_sha256(proposal(include_sealed=True))


def test_evaluate_promotion_rejects_failed_thresholds_with_reasons() -> None:
    strict = PromotionGatePolicy(
        id="reject",
        rules=[
            {
                "role": "validation",
                "min_total": 7,
                "min_candidate_score": 0.9,
                "min_absolute_improvement": 0.9,
                "min_discordant_pairs": 6,
                "max_one_sided_p_value": 0.01,
            }
        ],
    )
    decision = evaluate_promotion(strict, proposal())
    assert decision.decision is PromotionDecisionKind.REJECT
    assert decision.checks[0].passed is False
    assert len(decision.checks[0].reasons) == 5


def test_evaluate_rejects_missing_extra_and_mismatched_evidence() -> None:
    with pytest.raises(PromotionError, match="missing evidence roles"):
        evaluate_promotion(policy(include_sealed=True), proposal())

    with pytest.raises(PromotionError, match="undeclared evidence roles"):
        evaluate_promotion(policy(), proposal(include_sealed=True))

    item = proposal()
    item.evidence[0].baseline_variant_sha256 = "3" * 64
    with pytest.raises(PromotionError, match="baseline does not match"):
        evaluate_promotion(policy(), item)

    item = proposal()
    item.evidence[0].candidate_variant_sha256 = "3" * 64
    with pytest.raises(PromotionError, match="candidate does not match"):
        evaluate_promotion(policy(), item)


def test_evaluate_revalidates_mutated_models() -> None:
    gate = policy()
    gate.rules.append(gate.rules[0])
    with pytest.raises(PromotionError, match="roles must be unique"):
        evaluate_promotion(gate, proposal())


def test_materialize_promotion_embeds_exact_rollback_plan() -> None:
    decision = passing_decision()
    plan = rollback_plan(decision)
    result = materialize_promotion(decision, plan)

    assert result.active_variant_sha256 == CANDIDATE_SHA
    assert result.previous_variant_sha256 == BASE_SHA
    assert result.rollback_plan_sha256 == rollback_plan_sha256(plan)
    assert result.promotion_decision_sha256 == promotion_decision_sha256(decision)
    assert result.rollback == plan


def test_materialize_rejects_failed_decision_and_wrong_bindings() -> None:
    failed = evaluate_promotion(
        PromotionGatePolicy(id="fail", rules=[{"role": "validation", "min_total": 7}]),
        proposal(),
    )
    with pytest.raises(PromotionError, match="passing"):
        materialize_promotion(failed, rollback_plan(passing_decision()))

    decision = passing_decision()
    plan = rollback_plan(decision)
    plan.promotion_decision_sha256 = "3" * 64
    with pytest.raises(PromotionError, match="decision digest"):
        materialize_promotion(decision, plan)

    plan = rollback_plan(decision)
    plan.active_variant_sha256 = "3" * 64
    with pytest.raises(PromotionError, match="active variant"):
        materialize_promotion(decision, plan)

    plan = rollback_plan(decision)
    plan.target_variant_sha256 = "3" * 64
    with pytest.raises(PromotionError, match="target"):
        materialize_promotion(decision, plan)


def test_receipt_rejects_tampered_embedded_plan() -> None:
    payload = receipt().model_dump(mode="json")
    payload["rollback"]["target_locator"] = "variant://other"
    with pytest.raises(ValidationError, match="rollback digest"):
        PromotionReceipt.model_validate(payload)


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (RollbackOperator.LT, 0.05, True),
        (RollbackOperator.LTE, 0.1, True),
        (RollbackOperator.GT, 0.2, True),
        (RollbackOperator.GTE, 0.1, True),
    ],
)
def test_rollback_trigger_operators(kind: RollbackOperator, value: float, expected: bool) -> None:
    trigger = RollbackTrigger(
        id="metric",
        metric="error_rate",
        operator=kind,
        threshold=0.1,
        min_samples=2,
    )
    assert rollback_triggered(trigger, value, 2) is expected
    assert rollback_triggered(trigger, value, 1) is False


def test_rollback_trigger_rejects_invalid_observations() -> None:
    trigger = rollback_plan().triggers[0]
    with pytest.raises(PromotionError, match="finite"):
        rollback_triggered(trigger, float("nan"), 20)
    with pytest.raises(PromotionError, match="positive"):
        rollback_triggered(trigger, 0.2, 0)


def test_record_rollback_requires_declared_fired_trigger() -> None:
    promotion = receipt()
    event = record_rollback(
        "rollback-1",
        promotion,
        "error-rate",
        0.2,
        20,
        "operator",
        datetime(2026, 8, 7, tzinfo=UTC),
    )
    assert event.from_variant_sha256 == CANDIDATE_SHA
    assert event.to_variant_sha256 == BASE_SHA
    assert event.promotion_receipt_sha256 == promotion_receipt_sha256(promotion)

    with pytest.raises(PromotionError, match="not declared"):
        record_rollback(
            "rollback-2",
            promotion,
            "missing",
            0.2,
            20,
            "operator",
            datetime(2026, 8, 7, tzinfo=UTC),
        )
    with pytest.raises(PromotionError, match="does not satisfy"):
        record_rollback(
            "rollback-3",
            promotion,
            "error-rate",
            0.05,
            20,
            "operator",
            datetime(2026, 8, 7, tzinfo=UTC),
        )


def test_record_rollback_rejects_bad_ids_naive_time_and_mutation() -> None:
    promotion = receipt()
    with pytest.raises(PromotionError, match="stable identifiers"):
        record_rollback(
            "bad id",
            promotion,
            "error-rate",
            0.2,
            20,
            "operator",
            datetime(2026, 8, 7, tzinfo=UTC),
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        record_rollback(
            "rollback",
            promotion,
            "error-rate",
            0.2,
            20,
            "operator",
            datetime(2026, 8, 7),
        )

    promotion.rollback.target_variant_sha256 = "3" * 64
    with pytest.raises(PromotionError, match="invalid promotion receipt"):
        record_rollback(
            "rollback",
            promotion,
            "error-rate",
            0.2,
            20,
            "operator",
            datetime(2026, 8, 7, tzinfo=UTC),
        )


def test_models_reject_duplicate_triggers_unsafe_locator_and_nonfinite_values() -> None:
    payload = rollback_plan().model_dump(mode="json")
    payload["triggers"].append(payload["triggers"][0])
    with pytest.raises(ValidationError, match="trigger ids must be unique"):
        RollbackPlan.model_validate(payload)

    payload = rollback_plan().model_dump(mode="json")
    payload["target_locator"] = "bad\x00locator"
    with pytest.raises(ValidationError, match="must not contain NUL"):
        RollbackPlan.model_validate(payload)

    with pytest.raises(ValidationError, match="finite"):
        RollbackTrigger(
            id="bad",
            metric="error_rate",
            operator="gt",
            threshold=float("inf"),
        )


def test_digests_are_canonical_and_loaders_round_trip(tmp_path: Path) -> None:
    prediction = predictions(manifest())
    evidence = report()
    gate = policy()
    request = proposal()
    decision = passing_decision()
    plan = rollback_plan(decision)
    promotion = materialize_promotion(decision, plan)
    artifacts = [
        ("prediction", prediction, load_variant_predictions, variant_prediction_sha256),
        ("evidence", evidence, load_paired_evaluation, paired_evaluation_sha256),
        ("policy", gate, load_promotion_policy, promotion_policy_sha256),
        ("proposal", request, load_promotion_proposal, promotion_proposal_sha256),
        ("decision", decision, load_promotion_decision, promotion_decision_sha256),
        ("rollback", plan, load_rollback_plan, rollback_plan_sha256),
        ("receipt", promotion, load_promotion_receipt, promotion_receipt_sha256),
    ]
    for name, artifact, loader, digest in artifacts:
        path = tmp_path / f"{name}.yaml"
        path.write_text(
            yaml.safe_dump(artifact.model_dump(mode="json"), sort_keys=False),
            encoding="utf-8",
        )
        loaded = loader(path)
        assert loaded == artifact
        assert digest(loaded) == digest(artifact)


def test_loaders_reject_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "schema_version: '0.1'\nid: one\nid: two\nrules: []\n",
        encoding="utf-8",
    )
    with pytest.raises(PromotionError, match="duplicate"):
        load_promotion_policy(path)
