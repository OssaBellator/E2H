from __future__ import annotations

import pytest

from e2h.optimizer_adapters import (
    DSPyDatasetDocument,
    DSPyExample,
    OptimizerAdapterError,
    OptimizerFeedback,
    dspy_dataset_payload,
    dspy_example_payload,
    gepa_prediction_payload,
)


def _example(identifier: str = "example") -> DSPyExample:
    return DSPyExample(
        id=identifier,
        inputs={"task": "input"},
        outputs={"answer": "output"},
    )


def test_dspy_example_payload_revalidates_overlapping_fields() -> None:
    example = _example()
    example.outputs["task"] = "overwrites input"

    with pytest.raises(OptimizerAdapterError, match="invalid DSPy example"):
        dspy_example_payload(example)


def test_dspy_dataset_payload_revalidates_dataset_invariants() -> None:
    dataset = DSPyDatasetDocument(id="dataset", examples=[_example()])
    dataset.examples.append(dataset.examples[0])

    with pytest.raises(OptimizerAdapterError, match="invalid DSPy dataset"):
        dspy_dataset_payload(dataset)


def test_gepa_prediction_payload_revalidates_feedback() -> None:
    feedback = OptimizerFeedback(
        capsule_id="capsule",
        run_status="passed",
        score=1,
        feedback="all checks passed",
        checks=[],
    )
    feedback.score = 2

    with pytest.raises(OptimizerAdapterError, match="invalid optimizer feedback"):
        gepa_prediction_payload(feedback)
