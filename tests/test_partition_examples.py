from pathlib import Path

from e2h.optimizer_adapters import load_dspy_dataset
from e2h.partitions import (
    PartitionRole,
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


def test_committed_partition_examples_are_digest_bound_and_sealed() -> None:
    root = Path(__file__).parents[1]
    dataset = load_dspy_dataset(root / "examples/optimizer/dataset.yaml")
    manifest = load_dataset_partitions(root / "examples/optimizer/partition.yaml")
    predictions = load_sealed_predictions(root / "examples/optimizer/sealed-predictions.yaml")

    verification = verify_dataset_partitions(manifest, dataset)
    assert verification.dataset_sha256 == dspy_dataset_sha256(dataset)
    assert verification.public_dataset_sha256 == dspy_dataset_public_sha256(dataset)
    assert verification.partition_sha256 == dataset_partition_sha256(manifest)
    assert verification.public_partition_sha256 == dataset_partition_public_sha256(manifest)
    assert verification.train_examples == 1
    assert verification.validation_examples == 1
    assert verification.sealed_test_examples == 1

    training = export_dataset_partition(manifest, dataset, PartitionRole.TRAIN)
    sealed = export_dataset_partition(manifest, dataset, PartitionRole.SEALED_TEST)
    assert training.examples[0].values["expected_status"] == "passed"
    assert sealed.examples[0].values == {"task": "Evaluate the sealed harness candidate."}
    assert sealed.public_dataset_sha256 == verification.public_dataset_sha256
    assert sealed.public_partition_sha256 == verification.public_partition_sha256
    sealed_payload = sealed.model_dump(mode="json")
    assert "dataset_sha256" not in sealed_payload
    assert "partition_sha256" not in sealed_payload
    rendered = sealed.model_dump_json()
    assert "expected_status" not in rendered
    assert "private_note" not in rendered
    assert "must not be exported" not in rendered

    report = evaluate_sealed_predictions(manifest, dataset, predictions)
    assert report.total == 1
    assert report.correct == 1
    assert report.score == 1
    assert report.public_dataset_sha256 == verification.public_dataset_sha256
    assert report.public_partition_sha256 == verification.public_partition_sha256
    report_json = report.model_dump_json()
    assert "expected_status" not in report_json
    assert "sealed-task" not in report_json
