from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml
from typer.testing import CliRunner

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partition_cli import partition_app
from e2h.partitions import (
    DatasetPartitionDocument,
    SealedPredictionDocument,
    dataset_partition_public_sha256,
    dataset_partition_sha256,
    dspy_dataset_public_sha256,
    dspy_dataset_sha256,
)

app = typer.Typer()
app.add_typer(partition_app, name="partition")
runner = CliRunner()


def write_documents(tmp_path: Path) -> tuple[Path, Path, Path, DatasetPartitionDocument]:
    dataset = DSPyDatasetDocument.model_validate(
        {
            "id": "cli-partitions",
            "examples": [
                {
                    "id": "train",
                    "inputs": {"task": "train"},
                    "outputs": {"answer": "A"},
                },
                {
                    "id": "validation",
                    "inputs": {"task": "validation"},
                    "outputs": {"answer": "B"},
                },
                {
                    "id": "sealed",
                    "inputs": {"task": "sealed"},
                    "outputs": {"answer": "C"},
                    "metadata": {"secret": "withheld"},
                },
            ],
        }
    )
    manifest = DatasetPartitionDocument(
        id="cli-split",
        dataset_sha256=dspy_dataset_sha256(dataset),
        public_dataset_sha256=dspy_dataset_public_sha256(dataset),
        train=["train"],
        validation=["validation"],
        sealed_test=["sealed"],
    )
    predictions = SealedPredictionDocument.model_validate(
        {
            "public_dataset_sha256": manifest.public_dataset_sha256,
            "public_partition_sha256": dataset_partition_public_sha256(manifest),
            "predictions": [
                {"example_id": "sealed", "outputs": {"answer": "C"}},
            ],
        }
    )

    dataset_path = tmp_path / "dataset.yaml"
    manifest_path = tmp_path / "partition.yaml"
    prediction_path = tmp_path / "predictions.json"
    dataset_path.write_text(
        yaml.safe_dump(dataset.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    manifest_path.write_text(
        yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    prediction_path.write_text(predictions.model_dump_json(indent=2), encoding="utf-8")
    return manifest_path, dataset_path, prediction_path, manifest


def test_partition_cli_validate_digest_export_evaluate_and_schema(tmp_path: Path) -> None:
    manifest_path, dataset_path, prediction_path, manifest = write_documents(tmp_path)
    verification_path = tmp_path / "verification.json"
    export_path = tmp_path / "sealed-export.json"
    report_path = tmp_path / "sealed-report.json"
    schema_path = tmp_path / "partition-schema.json"

    result = runner.invoke(
        app,
        [
            "partition",
            "validate",
            str(manifest_path),
            str(dataset_path),
            "--output",
            str(verification_path),
        ],
    )
    assert result.exit_code == 0
    assert "E2H dataset partitions" in result.stdout
    assert "Public dataset SHA-256" in result.stdout
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    assert verification["sealed_test_examples"] == 1
    assert verification["public_dataset_sha256"] == manifest.public_dataset_sha256

    result = runner.invoke(app, ["partition", "digest", str(manifest_path)])
    assert result.exit_code == 0
    assert result.stdout.strip() == dataset_partition_sha256(manifest)

    result = runner.invoke(
        app,
        [
            "partition",
            "export",
            str(manifest_path),
            str(dataset_path),
            "--partition",
            "sealed_test",
            "--output",
            str(export_path),
        ],
    )
    assert result.exit_code == 0
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["labels_revealed"] is False
    assert exported["examples"][0]["values"] == {"task": "sealed"}
    assert exported["public_dataset_sha256"] == manifest.public_dataset_sha256
    assert exported["public_partition_sha256"] == dataset_partition_public_sha256(manifest)
    assert "dataset_sha256" not in exported
    assert "partition_sha256" not in exported
    assert "answer" not in export_path.read_text(encoding="utf-8")
    assert "withheld" not in export_path.read_text(encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "partition",
            "evaluate",
            str(manifest_path),
            str(dataset_path),
            str(prediction_path),
            "--output",
            str(report_path),
        ],
    )
    assert result.exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["correct"] == 1
    assert report["score"] == 1
    assert report["public_dataset_sha256"] == manifest.public_dataset_sha256
    assert "dataset_sha256" not in report
    assert "partition_sha256" not in report

    result = runner.invoke(
        app,
        [
            "partition",
            "schema",
            "--kind",
            "predictions",
            "--output",
            str(schema_path),
        ],
    )
    assert result.exit_code == 0
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["title"] == "SealedPredictionDocument"


def test_partition_cli_json_stdout_and_errors(tmp_path: Path) -> None:
    manifest_path, dataset_path, prediction_path, _ = write_documents(tmp_path)

    result = runner.invoke(
        app,
        [
            "partition",
            "validate",
            str(manifest_path),
            str(dataset_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["partition_id"] == "cli-split"

    result = runner.invoke(
        app,
        [
            "partition",
            "export",
            str(manifest_path),
            str(dataset_path),
            "--partition",
            "train",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["labels_revealed"] is True

    result = runner.invoke(
        app,
        [
            "partition",
            "evaluate",
            str(manifest_path),
            str(dataset_path),
            str(prediction_path),
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["total"] == 1

    result = runner.invoke(app, ["partition", "schema"])
    assert result.exit_code == 0
    assert "public_dataset_sha256" in result.stdout

    broken = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    broken["dataset_sha256"] = "0" * 64
    manifest_path.write_text(yaml.safe_dump(broken, sort_keys=False), encoding="utf-8")
    result = runner.invoke(
        app,
        ["partition", "validate", str(manifest_path), str(dataset_path)],
    )
    assert result.exit_code == 2
    assert "dataset digest" in result.stderr

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["partition", "digest", str(invalid)])
    assert result.exit_code == 2
    assert "Invalid dataset partitions" in result.stderr
