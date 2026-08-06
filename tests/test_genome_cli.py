from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml
from typer.testing import CliRunner

from e2h.genome import HarnessGenome, capsule_sha256
from e2h.genome_cli import genome_app
from e2h.models import TaskCapsule

app = typer.Typer()
app.add_typer(genome_app, name="genome")
runner = CliRunner()


def write_capsule(path: Path) -> TaskCapsule:
    capsule = TaskCapsule.model_validate(
        {
            "id": "cli-base",
            "goal": "Run baseline.",
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
    path.write_text(capsule.model_dump_json(indent=2), encoding="utf-8")
    return capsule


def write_genome(path: Path, capsule: TaskCapsule) -> HarnessGenome:
    genome = HarnessGenome.model_validate(
        {
            "id": "cli-candidate",
            "base_capsule_sha256": capsule_sha256(capsule),
            "patches": [
                {
                    "id": "argv",
                    "op": "check.argv.set",
                    "check_id": "contract",
                    "argv": ["python", "-c", "print('candidate')"],
                },
                {
                    "id": "env",
                    "op": "check.env.set",
                    "check_id": "contract",
                    "name": "MODE",
                    "value": "candidate",
                },
            ],
        }
    )
    path.write_text(yaml.safe_dump(genome.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return genome


def test_validate_apply_and_materialize_flow(tmp_path: Path) -> None:
    capsule_path = tmp_path / "capsule.json"
    genome_path = tmp_path / "genome.yaml"
    application_path = tmp_path / "application.json"
    materialized_path = tmp_path / "candidate.yaml"
    capsule = write_capsule(capsule_path)
    write_genome(genome_path, capsule)

    result = runner.invoke(
        app,
        ["genome", "validate", str(genome_path), str(capsule_path)],
    )
    assert result.exit_code == 0
    assert "Valid cli-candidate" in result.stdout
    assert "2 patches" in result.stdout

    result = runner.invoke(
        app,
        [
            "genome",
            "apply",
            str(genome_path),
            str(capsule_path),
            "--output",
            str(application_path),
        ],
    )
    assert result.exit_code == 0
    assert "E2H genome application" in result.stdout
    payload = json.loads(application_path.read_text(encoding="utf-8"))
    assert payload["genome_id"] == "cli-candidate"
    assert payload["applied_patch_ids"] == ["argv", "env"]

    result = runner.invoke(
        app,
        [
            "genome",
            "materialize",
            str(application_path),
            "--output",
            str(materialized_path),
        ],
    )
    assert result.exit_code == 0
    assert "Materialized cli-candidate" in result.stdout
    materialized = yaml.safe_load(materialized_path.read_text(encoding="utf-8"))
    command = materialized["success"]["commands"][0]
    assert command["argv"][-1] == "print('candidate')"
    assert command["env"] == {"MODE": "candidate"}


def test_apply_json_stdout_and_schema_output(tmp_path: Path) -> None:
    capsule_path = tmp_path / "capsule.json"
    genome_path = tmp_path / "genome.yaml"
    schema_path = tmp_path / "genome-schema.json"
    capsule = write_capsule(capsule_path)
    write_genome(genome_path, capsule)

    result = runner.invoke(
        app,
        ["genome", "apply", str(genome_path), str(capsule_path), "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["result_capsule_sha256"]

    result = runner.invoke(
        app,
        ["genome", "schema", "--output", str(schema_path)],
    )
    assert result.exit_code == 0
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["title"] == "HarnessGenome"

    result = runner.invoke(app, ["genome", "schema"])
    assert result.exit_code == 0
    assert "base_capsule_sha256" in result.stdout


def test_cli_returns_two_for_invalid_genome_and_materialization(tmp_path: Path) -> None:
    capsule_path = tmp_path / "capsule.json"
    genome_path = tmp_path / "genome.yaml"
    application_path = tmp_path / "application.json"
    capsule = write_capsule(capsule_path)
    write_genome(genome_path, capsule)

    wrong_capsule = TaskCapsule.model_validate(
        {
            "id": "wrong",
            "goal": "Wrong base.",
            "success": {"commands": [{"id": "contract", "argv": ["true"]}]},
        }
    )
    wrong_path = tmp_path / "wrong.json"
    wrong_path.write_text(wrong_capsule.model_dump_json(indent=2), encoding="utf-8")
    result = runner.invoke(
        app,
        ["genome", "validate", str(genome_path), str(wrong_path)],
    )
    assert result.exit_code == 2
    assert "base capsule digest" in result.stderr

    runner.invoke(
        app,
        [
            "genome",
            "apply",
            str(genome_path),
            str(capsule_path),
            "-o",
            str(application_path),
        ],
    )
    result = runner.invoke(
        app,
        [
            "genome",
            "materialize",
            str(application_path),
            "-o",
            str(tmp_path / "candidate.txt"),
        ],
    )
    assert result.exit_code == 2
    assert "must use .json, .yaml, or .yml" in result.stderr

    tampered = json.loads(application_path.read_text(encoding="utf-8"))
    tampered["capsule"]["goal"] = "tampered"
    application_path.write_text(json.dumps(tampered), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "genome",
            "materialize",
            str(application_path),
            "-o",
            str(tmp_path / "candidate.json"),
        ],
    )
    assert result.exit_code == 2
    assert "digest does not match" in result.stderr
