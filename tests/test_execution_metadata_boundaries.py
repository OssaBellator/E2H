from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.experiment import ExperimentError, ExperimentSpec, run_experiment
from e2h.models import TaskCapsule
from e2h.runner import RunnerError, run_capsule
from e2h.variants import HarnessVariant


def _capsule(metadata: dict[str, object] | None = None) -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "metadata-boundary",
            "goal": "Reject invalid execution metadata.",
            "success": {"commands": [{"id": "check", "argv": ["python", "-V"]}]},
            "metadata": metadata or {},
        }
    )


def _experiment(metadata: dict[str, object] | None = None) -> ExperimentSpec:
    return ExperimentSpec.model_validate(
        {
            "id": "metadata-boundary",
            "capsule": "capsule.json",
            "variants": [HarnessVariant(id="baseline")],
            "metadata": metadata or {},
        }
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"value": object()},
        {"value": float("inf")},
        {"value": {"unordered"}},
    ],
)
def test_task_capsule_rejects_noncanonical_metadata(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _capsule(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        {"value": object()},
        {"value": float("inf")},
        {"value": {"unordered"}},
    ],
)
def test_experiment_spec_rejects_noncanonical_metadata(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _experiment(metadata)


def test_runner_revalidates_mutated_capsule_metadata_before_execution(tmp_path: Path) -> None:
    capsule = _capsule()
    capsule.metadata["value"] = object()

    with pytest.raises(RunnerError, match="task capsule metadata"):
        run_capsule(capsule, tmp_path)


def test_experiment_revalidates_mutated_metadata_before_execution(tmp_path: Path) -> None:
    spec = _experiment()
    spec.metadata["value"] = object()

    with pytest.raises(ExperimentError, match="experiment metadata"):
        run_experiment(spec, _capsule(), tmp_path)


def test_execution_metadata_accepts_nested_canonical_json() -> None:
    metadata = {"suite": "smoke", "nested": {"enabled": True, "values": [1, 2.5, None]}}

    assert _capsule(metadata).metadata == metadata
    assert _experiment(metadata).metadata == metadata
