from __future__ import annotations

from typing import Any, cast

import pytest

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    DatasetPartitionError,
    PartitionRole,
    dspy_dataset_public_sha256,
    dspy_dataset_sha256,
    export_dataset_partition,
)


def _dataset() -> DSPyDatasetDocument:
    return DSPyDatasetDocument.model_validate(
        {
            "id": "partition-role-boundary",
            "examples": [
                {
                    "id": "train",
                    "inputs": {"task": "train"},
                    "outputs": {"answer": "TRAIN-LABEL"},
                },
                {
                    "id": "validation",
                    "inputs": {"task": "validation"},
                    "outputs": {"answer": "VALIDATION-LABEL"},
                },
                {
                    "id": "sealed",
                    "inputs": {"task": "sealed"},
                    "outputs": {"answer": "SEALED-SECRET"},
                },
            ],
        }
    )


def _manifest(source: DSPyDatasetDocument) -> DatasetPartitionDocument:
    return DatasetPartitionDocument(
        id="partition-role-boundary-split",
        dataset_sha256=dspy_dataset_sha256(source),
        public_dataset_sha256=dspy_dataset_public_sha256(source),
        train=["train"],
        validation=["validation"],
        sealed_test=["sealed"],
    )


@pytest.mark.parametrize("role", ["train", "validation", "sealed_test", object()])
def test_export_rejects_non_enum_roles_before_label_selection(role: Any) -> None:
    source = _dataset()
    split = _manifest(source)

    with pytest.raises(
        DatasetPartitionError,
        match="invalid partition role: expected PartitionRole",
    ):
        export_dataset_partition(split, source, cast(Any, role))


@pytest.mark.parametrize("role", ["train", "validation", "sealed_test", object()])
def test_ids_for_rejects_non_enum_roles(role: Any) -> None:
    split = _manifest(_dataset())

    with pytest.raises(
        DatasetPartitionError,
        match="invalid partition role: expected PartitionRole",
    ):
        split.ids_for(cast(Any, role))


def test_valid_partition_roles_preserve_label_policy() -> None:
    source = _dataset()
    split = _manifest(source)

    training = export_dataset_partition(split, source, PartitionRole.TRAIN)
    validation = export_dataset_partition(split, source, PartitionRole.VALIDATION)
    sealed = export_dataset_partition(split, source, PartitionRole.SEALED_TEST)

    assert [item.id for item in training.examples] == ["train"]
    assert training.labels_revealed is True
    assert training.examples[0].values["answer"] == "TRAIN-LABEL"

    assert [item.id for item in validation.examples] == ["validation"]
    assert validation.labels_revealed is True
    assert validation.examples[0].values["answer"] == "VALIDATION-LABEL"

    assert [item.id for item in sealed.examples] == ["sealed"]
    assert sealed.labels_revealed is False
    assert sealed.examples[0].values == {"task": "sealed"}
    assert "SEALED-SECRET" not in sealed.model_dump_json()
