from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.genome import GenomeApplication, HarnessGenome, apply_genome, capsule_sha256
from e2h.models import TaskCapsule


def _capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "genome-base",
            "goal": "Run the baseline checks.",
            "allowed_actions": {"tools": ["command"], "network": "deny"},
            "limits": {
                "max_commands": 10,
                "default_timeout_seconds": 30,
                "max_output_chars": 20_000,
            },
            "success": {
                "commands": [
                    {
                        "id": "contract",
                        "argv": ["python", "-c", "print('baseline')"],
                    }
                ]
            },
        }
    )


def test_genome_application_rejects_unchanged_base_digest() -> None:
    base = _capsule()
    genome = HarnessGenome.model_validate(
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
    application = apply_genome(genome, base)
    payload = application.model_dump(mode="python")
    payload["base_capsule_sha256"] = payload["result_capsule_sha256"]

    with pytest.raises(ValidationError, match="must change the base capsule"):
        GenomeApplication.model_validate(payload)


def test_apply_genome_produces_distinct_base_and_result_digests() -> None:
    base = _capsule()
    genome = HarnessGenome.model_validate(
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

    application = apply_genome(genome, base)

    assert application.base_capsule_sha256 != application.result_capsule_sha256
