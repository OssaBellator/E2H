from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.genome import CheckCwdSetPatch, GenomeError, HarnessGenome, genome_sha256

SHA = "a" * 64


def test_genome_cwd_patch_rejects_nul_path() -> None:
    with pytest.raises(ValidationError, match="path must not contain NUL"):
        CheckCwdSetPatch(id="cwd", check_id="check", value="bad\x00cwd")


def test_genome_digest_revalidates_mutated_nul_cwd_patch() -> None:
    genome = HarnessGenome.model_validate(
        {
            "id": "cwd-boundary",
            "base_capsule_sha256": SHA,
            "patches": [
                {
                    "id": "cwd",
                    "op": "check.cwd.set",
                    "check_id": "check",
                    "value": ".",
                }
            ],
        }
    )
    genome.patches[0].value = "bad\x00cwd"  # type: ignore[union-attr]

    with pytest.raises(GenomeError, match="invalid genome"):
        genome_sha256(genome)
