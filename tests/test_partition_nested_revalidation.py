from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.partitions import SealedPrediction, SealedPredictionDocument

SHA_A = "a" * 64
SHA_B = "b" * 64


def _document(prediction: SealedPrediction) -> SealedPredictionDocument:
    return SealedPredictionDocument(
        public_dataset_sha256=SHA_A,
        public_partition_sha256=SHA_B,
        predictions=[prediction],
    )


def test_sealed_prediction_document_revalidates_mutated_identifier() -> None:
    prediction = SealedPrediction(example_id="example", outputs={"answer": "A"})
    prediction.example_id = "invalid example id"

    with pytest.raises(ValidationError) as exc_info:
        _document(prediction)

    assert exc_info.value.errors()[0]["loc"][-1] == "example_id"


def test_sealed_prediction_document_revalidates_mutated_outputs() -> None:
    prediction = SealedPrediction(example_id="example", outputs={"answer": "A"})
    prediction.outputs = {}

    with pytest.raises(ValidationError, match="must define at least one output"):
        _document(prediction)
