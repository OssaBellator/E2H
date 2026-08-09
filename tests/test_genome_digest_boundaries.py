from __future__ import annotations

import pytest

from e2h.genome import GenomeError, HarnessGenome, capsule_sha256, genome_sha256
from e2h.models import TaskCapsule


def _capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "digest-base",
            "goal": "Run checks.",
            "allowed_actions": {"tools": ["command"], "network": "deny"},
            "limits": {
                "max_commands": 2,
                "default_timeout_seconds": 30,
                "max_output_chars": 2000,
            },
            "success": {
                "commands": [
                    {
                        "id": "check",
                        "argv": ["python", "-c", "print('ok')"],
                    }
                ]
            },
        }
    )


def test_capsule_digest_revalidates_post_validation_mutation() -> None:
    capsule = _capsule()
    capsule.allowed_actions.network = "invalid"  # type: ignore[assignment]

    with pytest.raises(GenomeError, match="invalid task capsule"):
        capsule_sha256(capsule)


def test_genome_digest_revalidates_post_validation_mutation() -> None:
    capsule = _capsule()
    genome = HarnessGenome.model_validate(
        {
            "id": "digest-genome",
            "base_capsule_sha256": capsule_sha256(capsule),
            "patches": [
                {
                    "id": "goal",
                    "op": "goal.set",
                    "value": "Updated goal.",
                }
            ],
        }
    )
    genome.patches.append(genome.patches[0])

    with pytest.raises(GenomeError, match="invalid genome"):
        genome_sha256(genome)
