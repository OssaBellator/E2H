from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.genome import CheckTimeoutSetPatch, GoalSetPatch, HarnessGenome

SHA = "a" * 64


def _genome(patch: GoalSetPatch | CheckTimeoutSetPatch) -> HarnessGenome:
    return HarnessGenome(
        id="candidate",
        base_capsule_sha256=SHA,
        patches=[patch],
    )


def test_genome_revalidates_mutated_patch_identifier() -> None:
    patch = GoalSetPatch(id="goal", value="Run candidate checks.")
    patch.id = "invalid patch id"

    with pytest.raises(ValidationError) as exc_info:
        _genome(patch)

    assert exc_info.value.errors()[0]["loc"][-1] == "id"


def test_genome_revalidates_mutated_patch_field_validator() -> None:
    patch = CheckTimeoutSetPatch(id="timeout", check_id="contract", seconds=30)
    patch.seconds = 0

    with pytest.raises(ValidationError, match="timeout seconds must be greater than zero"):
        _genome(patch)
