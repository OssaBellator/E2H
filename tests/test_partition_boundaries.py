from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    DatasetPartitionError,
    SealedPredictionDocument,
    dataset_partition_public_sha256,
    dspy_dataset_public_sha256,
    dspy_dataset_sha256,
    evaluate_sealed_predictions,
    verify_dataset_partitions,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _ManifestSubclass(DatasetPartitionDocument):
    pass


class _DatasetSubclass(DSPyDatasetDocument):
    pass


class _PredictionsSubclass(SealedPredictionDocument):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def dataset() -> DSPyDatasetDocument:
    return DSPyDatasetDocument.model_validate(
        {
            "id": "partition-boundary",
            "examples": [
                {
                    "id": "train",
                    "inputs": {"task": "train"},
                    "outputs": {"answer": "A"},
                },
                {
                    "id": "validation",
                    "inputs": {"task": "validation"},
                    "outputs": {"answer": "B"},
                },
                {
                    "id": "sealed",
                    "inputs": {"task": "sealed"},
                    "outputs": {"answer": "C"},
                },
            ],
            "metadata": {"purpose": "boundary"},
        }
    )


def manifest(source: DSPyDatasetDocument | None = None) -> DatasetPartitionDocument:
    source = source or dataset()
    return DatasetPartitionDocument(
        id="partition-boundary-split",
        dataset_sha256=dspy_dataset_sha256(source),
        public_dataset_sha256=dspy_dataset_public_sha256(source),
        train=["train"],
        validation=["validation"],
        sealed_test=["sealed"],
        metadata={"purpose": "boundary"},
    )


def predictions(split: DatasetPartitionDocument) -> SealedPredictionDocument:
    return SealedPredictionDocument.model_validate(
        {
            "public_dataset_sha256": split.public_dataset_sha256,
            "public_partition_sha256": dataset_partition_public_sha256(split),
            "predictions": [
                {"example_id": "sealed", "outputs": {"answer": "C"}},
            ],
        }
    )


def test_partition_boundary_rejects_subclasses_and_lookalikes() -> None:
    source = dataset()
    split = manifest(source)
    subclassed_manifest = _ManifestSubclass.model_validate(split.model_dump(mode="json"))
    subclassed_dataset = _DatasetSubclass.model_validate(source.model_dump(mode="json"))
    lookalike = _Lookalike.model_validate(split.model_dump(mode="json"))

    with pytest.raises(
        DatasetPartitionError,
        match="dataset partition manifest must be DatasetPartitionDocument",
    ):
        verify_dataset_partitions(subclassed_manifest, source)

    with pytest.raises(
        DatasetPartitionError,
        match="DSPy dataset must be DSPyDatasetDocument",
    ):
        verify_dataset_partitions(split, subclassed_dataset)

    with pytest.raises(
        DatasetPartitionError,
        match="dataset partition manifest must be DatasetPartitionDocument",
    ):
        verify_dataset_partitions(cast(Any, lookalike), source)


def test_sealed_evaluation_rejects_prediction_subclass() -> None:
    source = dataset()
    split = manifest(source)
    candidate = predictions(split)
    subclassed = _PredictionsSubclass.model_validate(candidate.model_dump(mode="json"))

    with pytest.raises(
        DatasetPartitionError,
        match="sealed predictions must be SealedPredictionDocument",
    ):
        evaluate_sealed_predictions(split, source, subclassed)


def test_partition_boundary_revalidates_overlap_mutation() -> None:
    source = dataset()
    split = manifest(source)
    split.train.append("validation")

    with pytest.raises(DatasetPartitionError, match="partitions must be disjoint"):
        verify_dataset_partitions(split, source)


def test_partition_boundary_preserves_invalid_manifest_metadata() -> None:
    source = dataset()
    split = manifest(source)
    split.metadata = {"invalid": {"set-value"}}

    with pytest.raises(DatasetPartitionError, match="invalid dataset partition inputs"):
        verify_dataset_partitions(split, source)


def test_partition_boundary_preserves_invalid_dataset_metadata() -> None:
    source = dataset()
    split = manifest(source)
    source.metadata = {"invalid": {"set-value"}}

    with pytest.raises(DatasetPartitionError, match="invalid dataset partition inputs"):
        verify_dataset_partitions(split, source)


def test_sealed_boundary_preserves_invalid_prediction_outputs() -> None:
    source = dataset()
    split = manifest(source)
    candidate = predictions(split)
    candidate.predictions[0].outputs = {"answer": {"set-value"}}

    with pytest.raises(DatasetPartitionError, match="invalid sealed predictions"):
        evaluate_sealed_predictions(split, source, candidate)


def test_partition_boundary_normalizes_raw_nested_dataset_assignment() -> None:
    source = dataset()
    split = manifest(source)
    source.examples = [example.model_dump(mode="json") for example in source.examples]

    verification = verify_dataset_partitions(split, source)

    assert verification.partition_id == split.id
    assert verification.dataset_id == source.id


def test_sealed_boundary_normalizes_raw_nested_prediction_assignment() -> None:
    source = dataset()
    split = manifest(source)
    candidate = predictions(split)
    candidate.predictions = [candidate.predictions[0].model_dump(mode="json")]

    report = evaluate_sealed_predictions(split, source, candidate)

    assert report.total == 1
    assert report.correct == 1
    assert report.score == 1
