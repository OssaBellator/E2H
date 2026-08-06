from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml
from typer.testing import CliRunner

from e2h.failures import unexpected_exit_failure
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.optimizer_adapters import (
    DSPyDatasetDocument,
    OptimizerAdapterDocument,
    OptimizerCandidateDocument,
    optimizer_adapter_sha256,
)
from e2h.optimizer_cli import optimizer_app
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.variants import HarnessVariant, HarnessVariantDocument, variant_sha256

app = typer.Typer()
app.add_typer(optimizer_app, name="optimizer")
runner = CliRunner()


def write_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    capsule = TaskCapsule.model_validate(
        {
            "id": "optimizer-cli",
            "goal": "Validate optimizer adapter CLI.",
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
                    "messages": [
                        {
                            "id": "system",
                            "role": "system",
                            "content": "Be exact.",
                        }
                    ],
                },
            }
        ),
    )
    variant_path = tmp_path / "variant.yaml"
    variant_path.write_text(
        yaml.safe_dump(variant.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    adapter = OptimizerAdapterDocument(
        id="adapter",
        optimizer="gepa",
        base_capsule_sha256=variant.base_capsule_sha256,
        base_variant_sha256=variant_sha256(variant.variant),
        components=[{"id": "instruction", "message_id": "system"}],
    )
    adapter_path = tmp_path / "adapter.yaml"
    adapter_path.write_text(
        yaml.safe_dump(adapter.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    candidate = OptimizerCandidateDocument(
        candidate_id="candidate",
        variant_id="optimized",
        optimizer="gepa",
        adapter_sha256=optimizer_adapter_sha256(adapter),
        base_capsule_sha256=adapter.base_capsule_sha256,
        base_variant_sha256=adapter.base_variant_sha256,
        updates=[
            {
                "component_id": "instruction",
                "content": "Be exact and use observable evidence.",
            }
        ],
    )
    candidate_path = tmp_path / "candidate.yaml"
    candidate_path.write_text(
        yaml.safe_dump(candidate.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return capsule_path, variant_path, adapter_path, candidate_path


def test_validate_apply_and_schema_flow(tmp_path: Path) -> None:
    capsule_path, variant_path, adapter_path, candidate_path = write_inputs(tmp_path)
    verification_path = tmp_path / "verification.json"
    optimized_path = tmp_path / "optimized.json"
    schema_path = tmp_path / "schema.json"

    result = runner.invoke(
        app,
        [
            "optimizer",
            "validate",
            str(adapter_path),
            str(capsule_path),
            str(variant_path),
            "--output",
            str(verification_path),
        ],
    )
    assert result.exit_code == 0
    assert "E2H optimizer adapter" in result.stdout
    assert json.loads(verification_path.read_text(encoding="utf-8"))["optimizer"] == "gepa"

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
            str(optimized_path),
        ],
    )
    assert result.exit_code == 0
    optimized = json.loads(optimized_path.read_text(encoding="utf-8"))
    assert optimized["variant"]["id"] == "optimized"
    assert "observable evidence" in optimized["variant"]["prompt"]["messages"][0]["content"]

    result = runner.invoke(
        app,
        [
            "optimizer",
            "schema",
            "--kind",
            "candidate",
            "--output",
            str(schema_path),
        ],
    )
    assert result.exit_code == 0
    assert json.loads(schema_path.read_text(encoding="utf-8"))["title"] == (
        "OptimizerCandidateDocument"
    )


def test_dataset_export_flow(tmp_path: Path) -> None:
    dataset = DSPyDatasetDocument(
        id="cli-dataset",
        examples=[
            {
                "id": "one",
                "inputs": {"question": "2+2"},
                "outputs": {"answer": "4"},
            }
        ],
    )
    dataset_path = tmp_path / "dataset.yaml"
    dataset_path.write_text(
        yaml.safe_dump(dataset.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    output_path = tmp_path / "dataset.json"

    result = runner.invoke(
        app,
        [
            "optimizer",
            "export-dataset",
            str(dataset_path),
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    exported = json.loads(output_path.read_text(encoding="utf-8"))
    assert exported == [
        {
            "input_fields": ["question"],
            "values": {"answer": "4", "question": "2+2"},
        }
    ]


def test_feedback_flow_and_prediction_payload(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    run = RunResult(
        capsule_id="feedback-cli",
        status=RunStatus.FAILED,
        started_at=now,
        finished_at=now,
        duration_seconds=0,
        checks=[
            CommandResult(
                id="contract",
                argv=["python"],
                cwd=".",
                status=CheckStatus.FAILED,
                exit_code=7,
                duration_seconds=0,
                stderr="do-not-export",
                failure=unexpected_exit_failure(7, [0]),
            )
        ],
        failure_summary={
            "total": 1,
            "evaluation_failures": 1,
            "by_category": {"task": 1},
            "by_code": {"unexpected_exit": 1},
            "primary_check_id": "contract",
            "primary_code": "unexpected_exit",
        },
    )
    run_path = tmp_path / "run.json"
    run_path.write_text(run.model_dump_json(indent=2), encoding="utf-8")

    result = runner.invoke(
        app,
        ["optimizer", "feedback", str(run_path), "--prediction"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["score"] == 0
    assert "unexpected_exit" in payload["feedback"]
    assert "do-not-export" not in payload["feedback"]


def test_invalid_adapter_and_run_return_two(tmp_path: Path) -> None:
    capsule_path, variant_path, adapter_path, _ = write_inputs(tmp_path)
    adapter_path.write_text("{}", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "optimizer",
            "validate",
            str(adapter_path),
            str(capsule_path),
            str(variant_path),
        ],
    )
    assert result.exit_code == 2
    assert "Invalid optimizer adapter" in result.stderr

    run_path = tmp_path / "run.json"
    run_path.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["optimizer", "feedback", str(run_path)])
    assert result.exit_code == 2
    assert "Invalid run result" in result.stderr
