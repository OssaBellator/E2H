from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from e2h.experiment import ExperimentSpec
from e2h.models import TaskCapsule
from e2h.variants import HarnessVariant


def _capsule(metadata: Any) -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "strict-metadata-json",
            "goal": "Reject JSON-coercible metadata.",
            "success": {"commands": [{"id": "check", "argv": ["python", "-V"]}]},
            "metadata": metadata,
        }
    )


def _experiment(metadata: Any) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "id": "strict-metadata-json",
            "capsule": "capsule.json",
            "variants": [HarnessVariant(id="baseline")],
            "metadata": metadata,
        }
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {1: "coerced top-level key"},
        {"nested": {1: "coerced nested key"}},
        {"nested": (1, 2)},
    ],
)
def test_task_capsule_rejects_json_coercible_metadata(metadata: Any) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _capsule(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        {1: "coerced top-level key"},
        {"nested": {1: "coerced nested key"}},
        {"nested": (1, 2)},
    ],
)
def test_experiment_rejects_json_coercible_metadata(metadata: Any) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _experiment(metadata)


def test_strict_metadata_preserves_nested_json_values() -> None:
    metadata = {
        "suite": "smoke",
        "nested": {"enabled": True, "values": [1, 2.5, None]},
    }

    assert _capsule(metadata).metadata == metadata
    assert _experiment(metadata).metadata == metadata
