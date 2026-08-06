from e2h import (
    DatasetPartitionDocument,
    DatasetPartitionError,
    PartitionRole,
    SealedPredictionDocument,
    dataset_partition_public_sha256,
    dspy_dataset_public_sha256,
    evaluate_sealed_predictions,
    export_dataset_partition,
    load_dataset_partitions,
    verify_dataset_partitions,
)
from e2h import partitions


def test_partition_contracts_are_available_from_package_root() -> None:
    assert DatasetPartitionDocument is partitions.DatasetPartitionDocument
    assert DatasetPartitionError is partitions.DatasetPartitionError
    assert PartitionRole is partitions.PartitionRole
    assert SealedPredictionDocument is partitions.SealedPredictionDocument
    assert dataset_partition_public_sha256 is (
        partitions.dataset_partition_public_sha256
    )
    assert dspy_dataset_public_sha256 is partitions.dspy_dataset_public_sha256
    assert evaluate_sealed_predictions is partitions.evaluate_sealed_predictions
    assert export_dataset_partition is partitions.export_dataset_partition
    assert load_dataset_partitions is partitions.load_dataset_partitions
    assert verify_dataset_partitions is partitions.verify_dataset_partitions
