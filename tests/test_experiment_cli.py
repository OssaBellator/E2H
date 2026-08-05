from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from e2h.cli import app

runner = CliRunner()


def write_experiment(path: Path, *, bad_variant: bool = False) -> None:
    variants = [
        "  - id: baseline",
        "    env:",
        "      EXPECTED_EXIT: '0'",
    ]
    if bad_variant:
        variants.extend(
            [
                "  - id: regression",
                "    env:",
                "      EXPECTED_EXIT: '3'",
            ]
        )
    path.write_text(
        "\n".join(
            [
                "id: cli-matrix",
                "capsule: capsule.yaml",
                "repetitions: 2",
                "variants:",
                *variants,
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_matrix_capsule(path: Path) -> None:
    code = "import os; raise SystemExit(int(os.environ['EXPECTED_EXIT']))"
    path.write_text(
        "\n".join(
            [
                "id: matrix-capsule",
                "goal: Test experiment CLI",
                "success:",
                "  commands:",
                "    - id: command",
                f"      argv: [{json.dumps(sys.executable)}, -c, {json.dumps(code)}]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_experiment_validate_command(tmp_path: Path) -> None:
    specification = tmp_path / "experiment.yaml"
    write_experiment(specification)
    result = runner.invoke(app, ["experiment", "validate", str(specification)])
    assert result.exit_code == 0
    assert "2 runs" in result.stdout


def test_experiment_run_writes_results_and_traces(tmp_path: Path) -> None:
    specification = tmp_path / "experiment.yaml"
    capsule = tmp_path / "capsule.yaml"
    output = tmp_path / "result.json"
    traces = tmp_path / "traces.jsonl"
    write_experiment(specification)
    write_matrix_capsule(capsule)

    result = runner.invoke(
        app,
        [
            "experiment",
            "run",
            str(specification),
            "--root",
            str(tmp_path),
            "--output",
            str(output),
            "--traces",
            str(traces),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload["runs"]) == 2
    assert payload["summaries"][0]["pass_rate"] == 1
    assert json.loads(output.read_text(encoding="utf-8"))["experiment_id"] == "cli-matrix"
    assert len(traces.read_text(encoding="utf-8").splitlines()) == 6


def test_experiment_run_table_and_require_all_pass(tmp_path: Path) -> None:
    specification = tmp_path / "experiment.yaml"
    capsule = tmp_path / "capsule.yaml"
    write_experiment(specification, bad_variant=True)
    write_matrix_capsule(capsule)

    result = runner.invoke(
        app,
        [
            "experiment",
            "run",
            str(specification),
            "--root",
            str(tmp_path),
            "--require-all-pass",
        ],
    )
    assert result.exit_code == 1
    assert "E2H experiment" in result.stdout
    assert "regression" in result.stdout


def test_experiment_invalid_spec_returns_two(tmp_path: Path) -> None:
    specification = tmp_path / "experiment.yaml"
    specification.write_text("id: invalid\n", encoding="utf-8")
    result = runner.invoke(app, ["experiment", "validate", str(specification)])
    assert result.exit_code == 2
    assert "Invalid experiment" in result.stderr


def test_experiment_missing_capsule_returns_two(tmp_path: Path) -> None:
    specification = tmp_path / "experiment.yaml"
    write_experiment(specification)
    result = runner.invoke(
        app,
        ["experiment", "run", str(specification), "--root", str(tmp_path)],
    )
    assert result.exit_code == 2
    assert "Unable to run experiment" in result.stderr
