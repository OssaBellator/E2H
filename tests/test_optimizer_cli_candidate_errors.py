from __future__ import annotations

from pathlib import Path

import typer
import yaml
from typer.testing import CliRunner

from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.optimizer_adapters import (
    OptimizerAdapterDocument,
    OptimizerCandidateDocument,
    optimizer_adapter_sha256,
)
from e2h.optimizer_cli import optimizer_app
from e2h.variants import HarnessVariant, HarnessVariantDocument, variant_sha256

app = typer.Typer()
app.add_typer(optimizer_app, name="optimizer")
runner = CliRunner()


def _write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def test_apply_reports_candidate_revalidation_as_usage_error(tmp_path: Path) -> None:
    capsule = TaskCapsule.model_validate(
        {
            "id": "optimizer-cli-invalid-candidate",
            "goal": "Reject a candidate that breaks the prompt contract.",
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
    capsule_path = tmp_path / "capsule.json"
    capsule_path.write_text(capsule.model_dump_json(indent=2), encoding="utf-8")

    variant = HarnessVariantDocument(
        base_capsule_sha256=capsule_sha256(capsule),
        variant=HarnessVariant.model_validate(
            {
                "id": "baseline",
                "prompt": {
                    "id": "prompt",
                    "variables": ["task"],
                    "messages": [
                        {
                            "id": "user",
                            "role": "user",
                            "content": "Execute ${task}.",
                        }
                    ],
                },
            }
        ),
    )
    variant_path = tmp_path / "variant.yaml"
    _write_yaml(variant_path, variant.model_dump(mode="json"))

    adapter = OptimizerAdapterDocument(
        id="adapter",
        optimizer="gepa",
        base_capsule_sha256=variant.base_capsule_sha256,
        base_variant_sha256=variant_sha256(variant.variant),
        components=[{"id": "instruction", "message_id": "user"}],
    )
    adapter_path = tmp_path / "adapter.yaml"
    _write_yaml(adapter_path, adapter.model_dump(mode="json"))

    candidate = OptimizerCandidateDocument(
        candidate_id="candidate",
        variant_id="invalid-optimized",
        optimizer="gepa",
        adapter_sha256=optimizer_adapter_sha256(adapter),
        base_capsule_sha256=adapter.base_capsule_sha256,
        base_variant_sha256=adapter.base_variant_sha256,
        updates=[{"component_id": "instruction", "content": "Execute the task."}],
    )
    candidate_path = tmp_path / "candidate.yaml"
    _write_yaml(candidate_path, candidate.model_dump(mode="json"))

    result = runner.invoke(
        app,
        [
            "optimizer",
            "apply",
            str(adapter_path),
            str(candidate_path),
            str(capsule_path),
            str(variant_path),
            "--output",
            str(tmp_path / "optimized.json"),
        ],
    )

    assert result.exit_code == 2
    assert "Invalid optimizer candidate" in result.stderr
    assert "unused variables" in result.stderr
