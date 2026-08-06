from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    DatasetPartitionError,
    PartitionRole,
    SealedPredictionDocument,
    dataset_partition_public_sha256,
    dataset_partition_sha256,
    dspy_dataset_public_sha256,
    dspy_dataset_sha256,
    evaluate_sealed_predictions,
    export_dataset_partition,
    load_dataset_partitions,
    load_sealed_predictions,
    verify_dataset_partitions,
)


def dataset() -> DSPyDatasetDocument:
    return DSPyDatasetDocument.model_validate(
        {
            "id": "partition-suite",
            "examples": [
                {
                    "id": "train-b",
                    "inputs": {"task": "train second"},
                    "outputs": {"answer": "B"},
                    "metadata": {"private_note": "train metadata"},
                },
                {
                    "id": "train-a",
                    "inputs": {"task": "train first"},
                    "outputs": {"answer": "A"},
                },
                {
                    "id": "validation-b",
                    "inputs": {"task": "validation second"},
                    "outputs": {"answer": "D"},
                },
                {
                    "id": "validation-a",
                    "inputs": {"task": "validation first"},
                    "outputs": {"answer": "C"},
                },
                {
                    "id": "sealed-b",
                    "inputs": {"task": "sealed second"},
                    "outputs": {"answer": "F"},
                    "metadata": {"private_note": "must not escape"},
                },
                {
                    "id": "sealed-a",
                    "inputs": {"task": "sealed first"},
                    "outputs": {"answer": "E"},
                },
            ],
            "metadata": {"purpose": "partition tests"},
        }
    )


def manifest(source: DSPyDatasetDocument | None = None) -> DatasetPartitionDocument:
    source = source or dataset()
    return DatasetPartitionDocument(
        id="three-way",
        dataset_sha256=dspy_dataset_sha256(source),
        public_dataset_sha256=dspy_dataset_public_sha256(source),
        train=["train-b", "train-a"],
        validation=["validation-b", "validation-a"],
        sealed_test=["sealed-b", "sealed-a"],
        metadata={"owner": "tests"},
    )


def predictions(
    split: DatasetPartitionDocument,
    *,
    sealed_a: str = "E",
    sealed_b: str = "F",
) -> SealedPredictionDocument:
    return SealedPredictionDocument.model_validate(
        {
            "public_dataset_sha256": split.public_dataset_sha256,
            "public_partition_sha256": dataset_partition_public_sha256(split),
            "predictions": [
                {"example_id": "sealed-a", "outputs": {"answer": sealed_a}},
                {"example_id": "sealed-b", "outputs": {"answer": sealed_b}},
            ],
        }
    )


def test_verify_partitions_normalizes_ids_and_reports_counts() -> None:
    source = dataset()
    split = manifest(source)

    verification = verify_dataset_partitions(split, source)

    assert split.train == ["train-a", "train-b"]
    assert split.validation == ["validation-a", "validation-b"]
    assert split.sealed_test == ["sealed-a", "sealed-b"]
    assert verification.dataset_sha256 == dspy_dataset_sha256(source)
    assert verification.public_dataset_sha256 == dspy_dataset_public_sha256(source)
    assert verification.partition_sha256 == dataset_partition_sha256(split)
    assert verification.public_partition_sha256 == dataset_partition_public_sha256(split)
    assert verification.train_examples == 2
    assert verification.validation_examples == 2
    assert verification.sealed_test_examples == 2
    assert verification.output_fields == ["answer"]


def test_exports_reveal_only_non_sealed_labels() -> None:
    source = dataset()
    split = manifest(source)

    training = export_dataset_partition(split, source, PartitionRole.TRAIN)
    validation = export_dataset_partition(split, source, PartitionRole.VALIDATION)
    sealed = export_dataset_partition(split, source, PartitionRole.SEALED_TEST)

    assert training.labels_revealed is True
    assert training.examples[0].id == "train-a"
    assert training.examples[0].values == {"answer": "A", "task": "train first"}
    assert validation.labels_revealed is True
    assert validation.examples[0].values["answer"] == "C"

    assert sealed.labels_revealed is False
    assert [item.id for item in sealed.examples] == ["sealed-a", "sealed-b"]
    assert sealed.examples[0].values == {"task": "sealed first"}
    assert sealed.public_dataset_sha256 == dspy_dataset_public_sha256(source)
    assert sealed.public_partition_sha256 == dataset_partition_public_sha256(split)
    payload = sealed.model_dump(mode="json")
    assert "dataset_sha256" not in payload
    assert "partition_sha256" not in payload
    rendered = sealed.model_dump_json()
    assert '"answer"' not in rendered
    assert "must not escape" not in rendered
    assert "private_note" not in rendered


def test_public_commitments_exclude_labels_and_metadata() -> None:
    source = dataset()
    changed_payload = source.model_dump(mode="json")
    for example in changed_payload["examples"]:
        if example["id"] == "sealed-a":
            example["outputs"]["answer"] = "different"
            example["metadata"]["private_note"] = "different private metadata"
    changed = DSPyDatasetDocument.model_validate(changed_payload)

    assert dspy_dataset_sha256(source) != dspy_dataset_sha256(changed)
    assert dspy_dataset_public_sha256(source) == dspy_dataset_public_sha256(changed)

    source_split = manifest(source)
    changed_split = manifest(changed)
    assert dataset_partition_sha256(source_split) != dataset_partition_sha256(changed_split)
    assert dataset_partition_public_sha256(source_split) == (
        dataset_partition_public_sha256(changed_split)
    )


def test_sealed_evaluation_returns_only_aggregate_score() -> None:
    source = dataset()
    split = manifest(source)

    perfect = evaluate_sealed_predictions(split, source, predictions(split))
    partial = evaluate_sealed_predictions(
        split,
        source,
        predictions(split, sealed_b="wrong"),
    )

    assert perfect.total == 2
    assert perfect.correct == 2
    assert perfect.score == 1
    assert partial.correct == 1
    assert partial.score == 0.5
    assert partial.public_dataset_sha256 == dspy_dataset_public_sha256(source)
    assert partial.public_partition_sha256 == dataset_partition_public_sha256(split)
    payload = partial.model_dump(mode="json")
    assert "dataset_sha256" not in payload
    assert "partition_sha256" not in payload
    rendered = partial.model_dump_json()
    assert '"E"' not in rendered
    assert '"F"' not in rendered
    assert "sealed-a" not in rendered
    assert "sealed-b" not in rendered


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("public_dataset_sha256", "0" * 64, "public dataset digest"),
        ("public_partition_sha256", "0" * 64, "public partition digest"),
    ],
)
def test_sealed_evaluation_rejects_wrong_bindings(
    field: str,
    value: str,
    message: str,
) -> None:
    source = dataset()
    split = manifest(source)
    payload = predictions(split).model_dump(mode="json")
    payload[field] = value
    bound = SealedPredictionDocument.model_validate(payload)

    with pytest.raises(DatasetPartitionError, match=message):
        evaluate_sealed_predictions(split, source, bound)


def test_sealed_evaluation_requires_exact_sealed_membership() -> None:
    source = dataset()
    split = manifest(source)
    payload = predictions(split).model_dump(mode="json")
    payload["predictions"].pop()

    with pytest.raises(DatasetPartitionError, match="missing examples"):
        evaluate_sealed_predictions(
            split,
            source,
            SealedPredictionDocument.model_validate(payload),
        )

    payload = predictions(split).model_dump(mode="json")
    payload["predictions"].append({"example_id": "train-a", "outputs": {"answer": "A"}})
    with pytest.raises(DatasetPartitionError, match="non-sealed"):
        evaluate_sealed_predictions(
            split,
            source,
            SealedPredictionDocument.model_validate(payload),
        )


def test_sealed_evaluation_requires_exact_output_signature() -> None:
    source = dataset()
    split = manifest(source)
    payload = predictions(split).model_dump(mode="json")
    payload["predictions"][0]["outputs"] = {"other": "E"}

    with pytest.raises(DatasetPartitionError, match="exactly these outputs"):
        evaluate_sealed_predictions(
            split,
            source,
            SealedPredictionDocument.model_validate(payload),
        )


def test_partition_verification_rejects_digest_unknown_and_unassigned_ids() -> None:
    source = dataset()

    wrong_digest = manifest(source)
    wrong_digest.dataset_sha256 = "0" * 64
    with pytest.raises(DatasetPartitionError, match="dataset digest"):
        verify_dataset_partitions(wrong_digest, source)

    wrong_public_digest = manifest(source)
    wrong_public_digest.public_dataset_sha256 = "0" * 64
    with pytest.raises(DatasetPartitionError, match="public dataset digest"):
        verify_dataset_partitions(wrong_public_digest, source)

    unknown = manifest(source)
    unknown.train[0] = "not-present"
    with pytest.raises(DatasetPartitionError, match="unknown"):
        verify_dataset_partitions(unknown, source)

    missing = manifest(source)
    missing.train.pop()
    with pytest.raises(DatasetPartitionError, match="does not assign every"):
        verify_dataset_partitions(missing, source)


def test_partition_verification_revalidates_mutated_models() -> None:
    source = dataset()
    split = manifest(source)
    split.train.append("validation-a")

    with pytest.raises(DatasetPartitionError, match="disjoint"):
        verify_dataset_partitions(split, source)


def test_partition_model_rejects_overlap_duplicates_and_bad_ids() -> None:
    source = dataset()
    payload = manifest(source).model_dump(mode="json")
    payload["validation"][0] = payload["train"][0]
    with pytest.raises(ValidationError, match="disjoint"):
        DatasetPartitionDocument.model_validate(payload)

    payload = manifest(source).model_dump(mode="json")
    payload["train"] = ["train-a", "train-a"]
    with pytest.raises(ValidationError, match="unique"):
        DatasetPartitionDocument.model_validate(payload)

    payload = manifest(source).model_dump(mode="json")
    payload["train"][0] = "bad id"
    with pytest.raises(ValidationError, match="stable identifiers"):
        DatasetPartitionDocument.model_validate(payload)


def test_partition_verification_requires_output_labels() -> None:
    source = DSPyDatasetDocument.model_validate(
        {
            "id": "unlabelled",
            "examples": [
                {"id": "train", "inputs": {"task": "a"}},
                {"id": "validation", "inputs": {"task": "b"}},
                {"id": "sealed", "inputs": {"task": "c"}},
            ],
        }
    )
    split = DatasetPartitionDocument(
        id="unlabelled-split",
        dataset_sha256=dspy_dataset_sha256(source),
        public_dataset_sha256=dspy_dataset_public_sha256(source),
        train=["train"],
        validation=["validation"],
        sealed_test=["sealed"],
    )
    with pytest.raises(DatasetPartitionError, match="at least one output"):
        verify_dataset_partitions(split, source)


def test_prediction_model_rejects_duplicate_ids_and_invalid_outputs() -> None:
    split = manifest()
    payload = predictions(split).model_dump(mode="json")
    payload["predictions"][1]["example_id"] = "sealed-a"
    with pytest.raises(ValidationError, match="unique"):
        SealedPredictionDocument.model_validate(payload)

    payload = predictions(split).model_dump(mode="json")
    payload["predictions"][0]["outputs"] = {}
    with pytest.raises(ValidationError, match="at least one output"):
        SealedPredictionDocument.model_validate(payload)

    payload = predictions(split).model_dump(mode="json")
    payload["predictions"][0]["outputs"] = {"bad key": "E"}
    with pytest.raises(ValidationError, match="Python identifiers"):
        SealedPredictionDocument.model_validate(payload)


def test_loaders_reject_duplicate_yaml_keys_and_round_trip(tmp_path: Path) -> None:
    split = manifest()
    manifest_path = tmp_path / "partition.yaml"
    manifest_path.write_text(
        yaml.safe_dump(split.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(
        predictions(split).model_dump_json(indent=2),
        encoding="utf-8",
    )

    assert load_dataset_partitions(manifest_path) == split
    assert load_sealed_predictions(prediction_path) == predictions(split)

    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "schema_version: '0.1'\nid: one\nid: two\n",
        encoding="utf-8",
    )
    with pytest.raises(DatasetPartitionError, match="duplicate"):
        load_dataset_partitions(duplicate)


def test_partition_metadata_is_bounded() -> None:
    payload = manifest().model_dump(mode="json")
    payload["metadata"] = {"large": "x" * 70_000}
    with pytest.raises(ValidationError, match="metadata exceeds"):
        DatasetPartitionDocument.model_validate(payload)
