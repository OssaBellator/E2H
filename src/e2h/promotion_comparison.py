"""Evaluator-side paired comparison for promotion evidence."""

from __future__ import annotations

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    dataset_partition_public_sha256,
    dspy_dataset_public_sha256,
    verify_dataset_partitions,
)
from e2h.promotion_models import (
    _ID_RE,
    PairedEvaluationReport,
    PromotionError,
    VariantPrediction,
    VariantPredictionDocument,
    _canonical_json_bytes,
)


def _validated_prediction_inputs(
    manifest: DatasetPartitionDocument,
    dataset: DSPyDatasetDocument,
    baseline: VariantPredictionDocument,
    candidate: VariantPredictionDocument,
) -> tuple[
    DatasetPartitionDocument,
    DSPyDatasetDocument,
    VariantPredictionDocument,
    VariantPredictionDocument,
]:
    try:
        return (
            DatasetPartitionDocument.model_validate(manifest.model_dump(mode="json")),
            DSPyDatasetDocument.model_validate(dataset.model_dump(mode="json")),
            VariantPredictionDocument.model_validate(baseline.model_dump(mode="json")),
            VariantPredictionDocument.model_validate(candidate.model_dump(mode="json")),
        )
    except ValueError as exc:
        raise PromotionError(f"invalid promotion comparison inputs: {exc}") from exc


def _prediction_map(
    document: VariantPredictionDocument,
    *,
    expected_ids: set[str],
    output_fields: set[str],
) -> dict[str, VariantPrediction]:
    predictions = {prediction.example_id: prediction for prediction in document.predictions}
    actual_ids = set(predictions)
    missing = sorted(expected_ids - actual_ids)
    unexpected = sorted(actual_ids - expected_ids)
    if missing:
        raise PromotionError(
            f"{document.variant_id} predictions are missing examples: " + ", ".join(missing)
        )
    if unexpected:
        raise PromotionError(
            f"{document.variant_id} predictions contain unexpected examples: "
            + ", ".join(unexpected)
        )
    for example_id, prediction in predictions.items():
        if set(prediction.outputs) != output_fields:
            raise PromotionError(
                f"{document.variant_id} prediction {example_id} must define exactly these outputs: "
                + ", ".join(sorted(output_fields))
            )
    return predictions


def compare_variant_predictions(
    evidence_id: str,
    manifest: DatasetPartitionDocument,
    dataset: DSPyDatasetDocument,
    baseline: VariantPredictionDocument,
    candidate: VariantPredictionDocument,
) -> PairedEvaluationReport:
    """Create aggregate paired correctness evidence without exposing labels."""
    if _ID_RE.fullmatch(evidence_id) is None:
        raise PromotionError("promotion evidence id must use a stable identifier")
    manifest, dataset, baseline, candidate = _validated_prediction_inputs(
        manifest,
        dataset,
        baseline,
        candidate,
    )
    verification = verify_dataset_partitions(manifest, dataset)
    if baseline.role is not candidate.role:
        raise PromotionError("baseline and candidate predictions must use the same partition role")
    if (
        baseline.variant_id == candidate.variant_id
        or baseline.variant_sha256 == candidate.variant_sha256
    ):
        raise PromotionError("baseline and candidate predictions must use different variants")
    for document in (baseline, candidate):
        if document.public_dataset_sha256 != verification.public_dataset_sha256:
            raise PromotionError(
                f"{document.variant_id} public dataset digest does not match the supplied dataset"
            )
        if document.public_partition_sha256 != verification.public_partition_sha256:
            raise PromotionError(
                f"{document.variant_id} public partition digest does not match "
                "the supplied manifest"
            )

    role = baseline.role
    example_ids = manifest.ids_for(role.partition_role())
    expected_ids = set(example_ids)
    output_fields = set(verification.output_fields)
    baseline_predictions = _prediction_map(
        baseline,
        expected_ids=expected_ids,
        output_fields=output_fields,
    )
    candidate_predictions = _prediction_map(
        candidate,
        expected_ids=expected_ids,
        output_fields=output_fields,
    )
    examples = {example.id: example for example in dataset.examples}

    both_correct = 0
    baseline_only_correct = 0
    candidate_only_correct = 0
    neither_correct = 0
    for example_id in example_ids:
        expected = _canonical_json_bytes(examples[example_id].outputs)
        baseline_correct = (
            _canonical_json_bytes(baseline_predictions[example_id].outputs) == expected
        )
        candidate_correct = (
            _canonical_json_bytes(candidate_predictions[example_id].outputs) == expected
        )
        if baseline_correct and candidate_correct:
            both_correct += 1
        elif baseline_correct:
            baseline_only_correct += 1
        elif candidate_correct:
            candidate_only_correct += 1
        else:
            neither_correct += 1

    total = len(example_ids)
    return PairedEvaluationReport(
        evidence_id=evidence_id,
        role=role,
        dataset_id=dataset.id,
        partition_id=manifest.id,
        public_dataset_sha256=dspy_dataset_public_sha256(dataset),
        public_partition_sha256=dataset_partition_public_sha256(manifest),
        baseline_variant_id=baseline.variant_id,
        baseline_variant_sha256=baseline.variant_sha256,
        candidate_variant_id=candidate.variant_id,
        candidate_variant_sha256=candidate.variant_sha256,
        total=total,
        both_correct=both_correct,
        baseline_only_correct=baseline_only_correct,
        candidate_only_correct=candidate_only_correct,
        neither_correct=neither_correct,
        baseline_score=(both_correct + baseline_only_correct) / total,
        candidate_score=(both_correct + candidate_only_correct) / total,
    )
