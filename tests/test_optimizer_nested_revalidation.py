from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.optimizer_adapters import (
    DSPyDatasetDocument,
    DSPyExample,
    OptimizerCandidateDocument,
    OptimizerKind,
    PromptCandidateUpdate,
)

SHA = "a" * 64


def test_dspy_dataset_revalidates_mutated_example() -> None:
    example = DSPyExample(id="example", inputs={"task": "value"})
    example.inputs = {"bad-field": "value"}

    with pytest.raises(ValidationError, match="DSPy input keys must be Python identifiers"):
        DSPyDatasetDocument(id="dataset", examples=[example])


def test_optimizer_candidate_revalidates_mutated_update() -> None:
    update = PromptCandidateUpdate(component_id="prompt", content="candidate")
    update.content = "bad\x00content"

    with pytest.raises(ValidationError, match="candidate prompt content must not contain NUL"):
        OptimizerCandidateDocument(
            candidate_id="candidate",
            variant_id="variant",
            optimizer=OptimizerKind.DSPY,
            adapter_sha256=SHA,
            base_capsule_sha256=SHA,
            base_variant_sha256=SHA,
            updates=[update],
        )
