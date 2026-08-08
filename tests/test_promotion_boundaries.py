from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar, cast

import pytest
from pydantic import BaseModel

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    dataset_partition_public_sha256,
    dspy_dataset_public_sha256,
    dspy_dataset_sha256,
)
from e2h.promotion import (
    PromotionDecision,
    PromotionDecisionKind,
    PromotionError,
    PromotionGatePolicy,
    PromotionProposal,
    PromotionReceipt,
    RollbackPlan,
    RollbackTrigger,
    VariantPredictionDocument,
    compare_variant_predictions,
    evaluate_promotion,
    materialize_promotion,
    promotion_decision_sha256,
    record_rollback,
    rollback_triggered,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

BASE_SHA = "1" * 64
CANDIDATE_SHA = "2" * 64
ModelT = TypeVar("ModelT", bound=BaseModel)


class _ManifestSubclass(DatasetPartitionDocument):
    pass


class _DatasetSubclass(DSPyDatasetDocument):
    pass


class _PredictionSubclass(VariantPredictionDocument):
    pass


class _PolicySubclass(PromotionGatePolicy):
    pass


class _ProposalSubclass(PromotionProposal):
    pass


class _DecisionSubclass(PromotionDecision):
    pass


class _RollbackPlanSubclass(RollbackPlan):
    pass


class _ReceiptSubclass(PromotionReceipt):
    pass


class _TriggerSubclass(RollbackTrigger):
    pass


def _as_subclass(value: BaseModel, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(value.model_dump(mode="json"))


def dataset() -> DSPyDatasetDocument:
    examples = [{"id": "train", "inputs": {"task": "train"}, "outputs": {"answer": "ok"}}]
    for role in ("validation", "sealed"):
        for index in range(6):
            examples.append(
                {
                    "id": f"{role}-{index}",
                    "inputs": {"task": f"{role} {index}"},
                    "outputs": {"answer": "ok"},
                }
            )
    return DSPyDatasetDocument.model_validate(
        {
            "id": "promotion-boundaries",
            "examples": examples,
        }
    )


def manifest(source: DSPyDatasetDocument | None = None) -> DatasetPartitionDocument:
    source = source or dataset()
    return DatasetPartitionDocument(
        id="promotion-boundary-split",
        dataset_sha256=dspy_dataset_sha256(source),
        public_dataset_sha256=dspy_dataset_public_sha256(source),
        train=["train"],
        validation=[f"validation-{index}" for index in range(6)],
        sealed_test=[f"sealed-{index}" for index in range(6)],
    )


def predictions(
    split: DatasetPartitionDocument,
    *,
    candidate: bool = False,
) -> VariantPredictionDocument:
    return VariantPredictionDocument.model_validate(
        {
            "variant_id": "candidate" if candidate else "baseline",
            "variant_sha256": CANDIDATE_SHA if candidate else BASE_SHA,
            "public_dataset_sha256": split.public_dataset_sha256,
            "public_partition_sha256": dataset_partition_public_sha256(split),
            "role": "validation",
            "predictions": [
                {
                    "example_id": f"validation-{index}",
                    "outputs": {"answer": "ok" if candidate and index < 5 else "wrong"},
                }
                for index in range(6)
            ],
        }
    )


def report() -> Any:
    source = dataset()
    split = manifest(source)
    return compare_variant_predictions(
        "validation-evidence",
        split,
        source,
        predictions(split),
        predictions(split, candidate=True),
    )


def policy() -> PromotionGatePolicy:
    return PromotionGatePolicy(
        id="strict-gate",
        rules=[
            {
                "role": "validation",
                "min_total": 6,
                "min_candidate_score": 0.8,
                "min_absolute_improvement": 0.8,
                "min_discordant_pairs": 5,
                "max_one_sided_p_value": 0.05,
            }
        ],
    )


def proposal() -> PromotionProposal:
    return PromotionProposal(
        id="candidate-promotion",
        baseline_variant_id="baseline",
        baseline_variant_sha256=BASE_SHA,
        candidate_variant_id="candidate",
        candidate_variant_sha256=CANDIDATE_SHA,
        evidence=[report()],
    )


def decision() -> PromotionDecision:
    return evaluate_promotion(policy(), proposal())


def rollback_plan(source: PromotionDecision | None = None) -> RollbackPlan:
    source = source or decision()
    return RollbackPlan(
        id="candidate-rollback",
        promotion_decision_sha256=promotion_decision_sha256(source),
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
                "min_samples": 20,
                "window_seconds": 600,
            }
        ],
    )


def receipt() -> PromotionReceipt:
    source = decision()
    return materialize_promotion(source, rollback_plan(source))


@pytest.mark.parametrize("kind", ["manifest", "dataset", "baseline", "candidate"])
def test_comparison_rejects_model_subclasses(kind: str) -> None:
    source = dataset()
    split = manifest(source)
    baseline = predictions(split)
    candidate = predictions(split, candidate=True)
    args: list[Any] = [split, source, baseline, candidate]
    if kind == "manifest":
        args[0] = _as_subclass(split, _ManifestSubclass)
        expected = "dataset partition manifest must be DatasetPartitionDocument"
    elif kind == "dataset":
        args[1] = _as_subclass(source, _DatasetSubclass)
        expected = "DSPy dataset must be DSPyDatasetDocument"
    elif kind == "baseline":
        args[2] = _as_subclass(baseline, _PredictionSubclass)
        expected = "baseline predictions must be VariantPredictionDocument"
    else:
        args[3] = _as_subclass(candidate, _PredictionSubclass)
        expected = "candidate predictions must be VariantPredictionDocument"

    with pytest.raises(PromotionError, match=expected):
        compare_variant_predictions("boundary", *cast(tuple[Any, Any, Any, Any], tuple(args)))


def test_evaluation_rejects_model_subclasses_and_plain_wrong_types() -> None:
    with pytest.raises(PromotionError, match="promotion policy must be PromotionGatePolicy"):
        evaluate_promotion(_as_subclass(policy(), _PolicySubclass), proposal())
    with pytest.raises(PromotionError, match="promotion proposal must be PromotionProposal"):
        evaluate_promotion(policy(), _as_subclass(proposal(), _ProposalSubclass))
    with pytest.raises(PromotionError, match="promotion policy must be PromotionGatePolicy"):
        evaluate_promotion(cast(Any, object()), proposal())


def test_evaluation_normalizes_warning_prone_post_validation_assignment() -> None:
    gate = policy()
    gate.rules = [gate.rules[0].model_dump(mode="json")]

    result = evaluate_promotion(gate, proposal())

    assert result.decision is PromotionDecisionKind.PROMOTE


def test_materialization_rejects_model_subclasses() -> None:
    source = decision()
    plan = rollback_plan(source)
    with pytest.raises(PromotionError, match="promotion decision must be PromotionDecision"):
        materialize_promotion(_as_subclass(source, _DecisionSubclass), plan)
    with pytest.raises(PromotionError, match="rollback plan must be RollbackPlan"):
        materialize_promotion(source, _as_subclass(plan, _RollbackPlanSubclass))


def test_rollback_trigger_revalidates_mutations_and_rejects_subclasses() -> None:
    trigger = rollback_plan().triggers[0]
    trigger.min_samples = 0
    with pytest.raises(PromotionError, match="invalid rollback trigger"):
        rollback_triggered(trigger, 0.2, 1)

    valid = rollback_plan().triggers[0]
    subclassed = _as_subclass(valid, _TriggerSubclass)
    with pytest.raises(PromotionError, match="rollback trigger must be RollbackTrigger"):
        rollback_triggered(subclassed, 0.2, 20)


def test_record_rollback_rejects_receipt_subclasses() -> None:
    subclassed = _as_subclass(receipt(), _ReceiptSubclass)
    with pytest.raises(PromotionError, match="promotion receipt must be PromotionReceipt"):
        record_rollback(
            "rollback-boundary",
            subclassed,
            "error-rate",
            0.2,
            20,
            "operator",
            datetime(2026, 8, 8, tzinfo=UTC),
        )
