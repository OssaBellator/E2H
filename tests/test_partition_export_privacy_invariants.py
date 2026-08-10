from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    DatasetPartitionExport,
    PartitionExamplePayload,
    PartitionRole,
    dspy_dataset_public_sha256,
    dspy_dataset_sha256,
    export_dataset_partition,
)

SHA = "a" * 64


def _payload(
    identifier: str = "example",
    *,
    values: dict[str, Any] | None = None,
    input_fields: list[str] | None = None,
) -> PartitionExamplePayload:
    return PartitionExamplePayload(
        id=identifier,
        values=values or {"task": "value"},
        input_fields=input_fields or ["task"],
    )


def _export(
    *,
    role: PartitionRole = PartitionRole.SEALED_TEST,
    labels_revealed: bool = False,
    examples: list[PartitionExamplePayload] | None = None,
) -> DatasetPartitionExport:
    return DatasetPartitionExport(
        partition_id="partition",
        public_partition_sha256=SHA,
        dataset_id="dataset",
        public_dataset_sha256=SHA,
        role=role,
        labels_revealed=labels_revealed,
        examples=examples or [_payload()],
    )


@pytest.mark.parametrize(
    ("role", "labels_revealed"),
    [
        (PartitionRole.SEALED_TEST, True),
        (PartitionRole.TRAIN, False),
        (PartitionRole.VALIDATION, False),
    ],
)
def test_export_rejects_label_flag_inconsistent_with_role(
    role: PartitionRole,
    labels_revealed: bool,
) -> None:
    with pytest.raises(ValidationError, match="labels_revealed must match partition role"):
        _export(role=role, labels_revealed=labels_revealed)


def test_sealed_export_rejects_undeclared_value_fields() -> None:
    payload = _payload(values={"task": "value", "answer": "SECRET"})

    with pytest.raises(ValidationError, match="sealed partition exports must contain input fields only"):
        _export(examples=[payload])


def test_labelled_export_accepts_output_fields() -> None:
    payload = _payload(values={"task": "value", "answer": "label"})

    exported = _export(
        role=PartitionRole.TRAIN,
        labels_revealed=True,
        examples=[payload],
    )

    assert exported.examples[0].values["answer"] == "label"


def test_export_rejects_duplicate_example_ids() -> None:
    with pytest.raises(ValidationError, match="example ids must be unique"):
        _export(examples=[_payload("same"), _payload("same")])


def test_payload_requires_sorted_unique_input_fields() -> None:
    with pytest.raises(ValidationError, match="input fields must be unique and sorted"):
        _payload(
            values={"alpha": 1, "beta": 2},
            input_fields=["beta", "alpha"],
        )

    with pytest.raises(ValidationError, match="input fields must be unique and sorted"):
        _payload(input_fields=["task", "task"])


def test_payload_requires_every_declared_input_value() -> None:
    with pytest.raises(ValidationError, match="contain every declared input field"):
        _payload(values={"other": "value"}, input_fields=["task"])


def test_payload_requires_canonical_identifier_values() -> None:
    with pytest.raises(ValidationError, match="value keys must be Python identifiers"):
        _payload(values={"not-a-field": "value"})

    with pytest.raises(ValidationError, match="canonical JSON data"):
        _payload(values={"task": ("python", "tuple")})


def test_export_revalidates_mutated_payload_instances() -> None:
    payload = _payload()
    payload.input_fields = ["task", "task"]

    with pytest.raises(ValidationError, match="input fields must be unique and sorted"):
        _export(examples=[payload])


def test_generated_exports_preserve_label_policy() -> None:
    dataset = DSPyDatasetDocument.model_validate(
        {
            "id": "privacy-dataset",
            "examples": [
                {"id": "train", "inputs": {"task": "a"}, "outputs": {"answer": "A"}},
                {
                    "id": "validation",
                    "inputs": {"task": "b"},
                    "outputs": {"answer": "B"},
                },
                {"id": "sealed", "inputs": {"task": "c"}, "outputs": {"answer": "SECRET"}},
            ],
        }
    )
    partition = DatasetPartitionDocument(
        id="privacy-partition",
        dataset_sha256=dspy_dataset_sha256(dataset),
        public_dataset_sha256=dspy_dataset_public_sha256(dataset),
        train=["train"],
        validation=["validation"],
        sealed_test=["sealed"],
    )

    training = export_dataset_partition(partition, dataset, PartitionRole.TRAIN)
    validation = export_dataset_partition(partition, dataset, PartitionRole.VALIDATION)
    sealed = export_dataset_partition(partition, dataset, PartitionRole.SEALED_TEST)

    assert training.labels_revealed is True
    assert validation.labels_revealed is True
    assert sealed.labels_revealed is False
    assert sealed.examples[0].values == {"task": "c"}
    assert "SECRET" not in sealed.model_dump_json()
