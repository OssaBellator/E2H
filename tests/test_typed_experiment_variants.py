from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.experiment import ExperimentSpec, HarnessVariant, run_experiment
from e2h.models import TaskCapsule
from e2h.variants import PromptVariant, variant_sha256


def test_typed_variant_identity_is_persisted_and_injected(tmp_path: Path) -> None:
    code = "import os; print(os.environ['E2H_VARIANT_ID']); print(os.environ['E2H_VARIANT_SHA256'])"
    capsule = TaskCapsule.model_validate(
        {
            "id": "typed-variant",
            "goal": "Record typed variant provenance.",
            "success": {
                "commands": [
                    {
                        "id": "identity",
                        "argv": [sys.executable, "-c", code],
                    }
                ]
            },
        }
    )
    variant = HarnessVariant(
        id="candidate",
        prompt=PromptVariant.model_validate(
            {
                "id": "prompt",
                "messages": [{"id": "system", "role": "system", "content": "Be exact."}],
            }
        ),
    )
    execution = run_experiment(
        ExperimentSpec(id="typed", capsule="capsule.yaml", variants=[variant]),
        capsule,
        tmp_path,
    )

    digest = variant_sha256(variant)
    run = execution.result.runs[0]
    assert run.variant_sha256 == digest
    assert run.result.checks[0].stdout == f"candidate\n{digest}\n"


def test_experiment_variant_rejects_digest_spoofing() -> None:
    with pytest.raises(ValidationError, match="reserved"):
        HarnessVariant(
            id="spoofed",
            env={"E2H_VARIANT_SHA256": "0" * 64},
        )
