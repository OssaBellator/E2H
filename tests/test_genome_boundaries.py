from __future__ import annotations

from typing import TypeVar

import pytest
from pydantic import BaseModel

from e2h.genome import (
    GenomeApplication,
    GenomeError,
    GoalSetPatch,
    HarnessGenome,
    apply_genome,
    capsule_sha256,
    materialize_application,
)
from e2h.models import TaskCapsule

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

ModelT = TypeVar("ModelT", bound=BaseModel)


class _GenomeSubclass(HarnessGenome):
    pass


class _CapsuleSubclass(TaskCapsule):
    pass


class _ApplicationSubclass(GenomeApplication):
    pass


def _as_subclass(value: BaseModel, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(value.model_dump(mode="json"))


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "genome-boundary",
            "goal": "Run the baseline checks.",
            "success": {
                "commands": [
                    {
                        "id": "contract",
                        "argv": ["python", "-c", "print('ok')"],
                    }
                ]
            },
        }
    )


def genome(base: TaskCapsule | None = None) -> HarnessGenome:
    base = base or capsule()
    return HarnessGenome.model_validate(
        {
            "id": "candidate",
            "base_capsule_sha256": capsule_sha256(base),
            "patches": [
                {
                    "id": "goal",
                    "op": "goal.set",
                    "value": "Run the candidate checks.",
                }
            ],
        }
    )


def test_apply_revalidates_mutated_genome_cross_fields() -> None:
    base = capsule()
    candidate = genome(base)
    candidate.patches.append(
        GoalSetPatch(
            id="goal-again",
            value="Run a second candidate goal.",
        )
    )

    with pytest.raises(GenomeError, match="same target"):
        apply_genome(candidate, base)


def test_apply_revalidates_base_capsule_before_digest_binding() -> None:
    base = capsule()
    candidate = genome(base)
    base.goal = ""

    with pytest.raises(GenomeError, match="invalid task capsule"):
        apply_genome(candidate, base)


def test_apply_rejects_genome_and_capsule_subclasses() -> None:
    base = capsule()
    candidate = genome(base)

    with pytest.raises(GenomeError, match="expected HarnessGenome, got _GenomeSubclass"):
        apply_genome(_as_subclass(candidate, _GenomeSubclass), base)

    with pytest.raises(GenomeError, match="expected TaskCapsule, got _CapsuleSubclass"):
        apply_genome(candidate, _as_subclass(base, _CapsuleSubclass))


def test_apply_normalizes_warning_prone_patch_assignment() -> None:
    base = capsule()
    candidate = genome(base)
    candidate.patches = [
        {
            "id": "goal",
            "op": "goal.set",
            "value": "Run the normalized candidate checks.",
        }
    ]

    result = apply_genome(candidate, base)

    assert result.capsule.goal == "Run the normalized candidate checks."


def test_materialize_rejects_application_subclasses() -> None:
    base = capsule()
    application = apply_genome(genome(base), base)
    subclassed = _as_subclass(application, _ApplicationSubclass)

    with pytest.raises(GenomeError, match="expected GenomeApplication, got _ApplicationSubclass"):
        materialize_application(subclassed)
