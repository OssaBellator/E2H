from __future__ import annotations

from collections.abc import Callable

import pytest

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    DatasetPartitionError,
    dataset_partition_public_sha256,
    dataset_partition_sha256,
    dspy_dataset_public_sha256,
    dspy_dataset_sha256,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _DatasetSubclass(DSPyDatasetDocument):
    pass


class _PartitionSubclass(DatasetPartitionDocument):
    pass


def dataset() -> DSPyDatasetDocument:
    return DSPyDatasetDocument.model_validate(
        {
            "id": "partition-digest-boundary",
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
            "metadata": {"purpose": "digest-boundary"},
        }
    )


def manifest(source: DSPyDatasetDocument) -> DatasetPartitionDocument:
    return DatasetPartitionDocument(
        id="partition-digest-boundary-split",
        dataset_sha256=dspy_dataset_sha256(source),
        public_dataset_sha256=dspy_dataset_public_sha256(source),
        train=["train"],
        validation=["validation"],
        sealed_test=["sealed"],
        metadata={"purpose": "digest-boundary"},
    )


@pytest.mark.parametrize(
    "digest",
    [dspy_dataset_sha256, dspy_dataset_public_sha256],
)
def test_dataset_digest_revalidates_empty_examples(
    digest: Callable[[DSPyDatasetDocument], str],
) -> None:
    candidate = dataset()
    candidate.examples = []

    with pytest.raises(DatasetPartitionError, match="invalid DSPy dataset"):
        digest(candidate)


@pytest.mark.parametrize(
    "digest",
    [dspy_dataset_sha256, dspy_dataset_public_sha256],
)
def test_dataset_digest_revalidates_parent_signature_contract(
    digest: Callable[[DSPyDatasetDocument], str],
) -> None:
    candidate = dataset()
    candidate.examples[1].outputs = {"different": "B"}

    with pytest.raises(DatasetPartitionError, match="identical output fields"):
        digest(candidate)


@pytest.mark.parametrize(
    "digest",
    [dataset_partition_sha256, dataset_partition_public_sha256],
)
def test_partition_digest_revalidates_overlap(
    digest: Callable[[DatasetPartitionDocument], str],
) -> None:
    source = dataset()
    candidate = manifest(source)
    candidate.validation.append("train")

    with pytest.raises(DatasetPartitionError, match="partitions must be disjoint"):
        digest(candidate)


def test_digest_boundaries_reject_model_subclasses() -> None:
    source = dataset()
    split = manifest(source)
    dataset_subclass = _DatasetSubclass.model_validate(source.model_dump(mode="json"))
    partition_subclass = _PartitionSubclass.model_validate(split.model_dump(mode="json"))

    with pytest.raises(
        DatasetPartitionError,
        match="DSPy dataset must be DSPyDatasetDocument",
    ):
        dspy_dataset_sha256(dataset_subclass)

    with pytest.raises(
        DatasetPartitionError,
        match="dataset partition manifest must be DatasetPartitionDocument",
    ):
        dataset_partition_sha256(partition_subclass)


def test_digest_boundaries_preserve_canonical_invalid_metadata() -> None:
    source = dataset()
    source.metadata = {"invalid": {"set-value"}}

    with pytest.raises(DatasetPartitionError, match="canonical JSON"):
        dspy_dataset_sha256(source)

    normalized_source = dataset()
    split = manifest(normalized_source)
    split.metadata = {"invalid": {"set-value"}}

    with pytest.raises(DatasetPartitionError, match="canonical JSON"):
        dataset_partition_sha256(split)


def test_dataset_digest_boundaries_normalize_raw_nested_assignments() -> None:
    source = dataset()
    expected_private = dspy_dataset_sha256(source)
    expected_public = dspy_dataset_public_sha256(source)
    source.examples = [example.model_dump(mode="json") for example in source.examples]

    assert dspy_dataset_sha256(source) == expected_private
    assert dspy_dataset_public_sha256(source) == expected_public
