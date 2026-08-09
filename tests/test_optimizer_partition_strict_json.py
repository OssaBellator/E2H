from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from e2h.optimizer_adapters import (
    DSPyDatasetDocument,
    DSPyExample,
    OptimizerAdapterDocument,
    OptimizerAdapterError,
    PromptComponentBinding,
    dspy_dataset_payload,
)
from e2h.partitions import (
    DatasetPartitionDocument,
    DatasetPartitionError,
    SealedPrediction,
    dspy_dataset_sha256,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _example(*, task: Any = "input", answer: Any = "output") -> DSPyExample:
    return DSPyExample(
        id="example",
        inputs={"task": task},
        outputs={"answer": answer},
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("inputs", {"nested": {1: "coerced key"}}),
        ("inputs", {"nested": (1, 2)}),
        ("outputs", {"nested": {1: "coerced key"}}),
        ("outputs", {"nested": (1, 2)}),
    ],
)
def test_dspy_example_rejects_json_coercible_values(field: str, value: Any) -> None:
    payload: dict[str, Any] = {
        "id": "example",
        "inputs": {"task": "input"},
        "outputs": {"answer": "output"},
    }
    payload[field] = {"task" if field == "inputs" else "answer": value}

    with pytest.raises(ValidationError, match="canonical JSON data"):
        DSPyExample.model_validate(payload)


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_optimizer_metadata_rejects_json_coercible_values(metadata: Any) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        DSPyDatasetDocument(id="dataset", examples=[_example()], metadata=metadata)

    with pytest.raises(ValidationError, match="canonical JSON data"):
        OptimizerAdapterDocument(
            id="adapter",
            optimizer="dspy",
            base_capsule_sha256=SHA_A,
            base_variant_sha256=SHA_B,
            components=[PromptComponentBinding(id="prompt", message_id="system")],
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_partition_metadata_rejects_json_coercible_values(metadata: Any) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        DatasetPartitionDocument(
            id="partition",
            dataset_sha256=SHA_A,
            public_dataset_sha256=SHA_B,
            train=["train"],
            validation=["validation"],
            sealed_test=["sealed"],
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "outputs",
    [
        {"answer": {"nested": {1: "coerced key"}}},
        {"answer": {"nested": (1, 2)}},
    ],
)
def test_sealed_predictions_reject_json_coercible_outputs(outputs: Any) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        SealedPrediction(example_id="sealed", outputs=outputs)


def test_dataset_boundaries_reject_mutated_json_coercion() -> None:
    dataset = DSPyDatasetDocument(id="dataset", examples=[_example()])
    dataset.examples[0].inputs["task"] = {"nested": {1: "coerced key"}}

    with pytest.raises(OptimizerAdapterError, match="invalid DSPy dataset"):
        dspy_dataset_payload(dataset)

    with pytest.raises(DatasetPartitionError, match="invalid DSPy dataset"):
        dspy_dataset_sha256(dataset)


def test_optimizer_and_partition_models_preserve_exact_nested_json() -> None:
    nested = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}
    example = _example(task=nested, answer=nested)
    dataset = DSPyDatasetDocument(id="dataset", examples=[example], metadata=nested)
    partition = DatasetPartitionDocument(
        id="partition",
        dataset_sha256=SHA_A,
        public_dataset_sha256=SHA_B,
        train=["train"],
        validation=["validation"],
        sealed_test=["sealed"],
        metadata=nested,
    )
    prediction = SealedPrediction(example_id="sealed", outputs={"answer": nested})

    assert dataset.examples[0].inputs["task"] == nested
    assert dataset.metadata == nested
    assert partition.metadata == nested
    assert prediction.outputs["answer"] == nested
