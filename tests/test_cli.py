from __future__ import annotations

import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from e2h.cli import app

runner = CliRunner()


def write_capsule(path: Path, *, exit_code: int = 0) -> None:
    path.write_text(
        "\n".join(
            [
                "id: cli-test",
                "goal: Test the CLI",
                "success:",
                "  commands:",
                "    - id: command",
                "      argv: "
                f"[{json.dumps(sys.executable)}, -c, "
                f"{json.dumps(f'raise SystemExit({exit_code})')} ]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_validate_command(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule.yaml"
    write_capsule(capsule)
    result = runner.invoke(app, ["validate", str(capsule)])
    assert result.exit_code == 0
    assert "Valid" in result.stdout


def test_run_json_and_output_file(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule.yaml"
    output = tmp_path / "result.json"
    write_capsule(capsule)
    result = runner.invoke(
        app,
        ["run", str(capsule), "--workspace", str(tmp_path), "--json", "--output", str(output)],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert output.exists()


def test_run_returns_one_for_failed_checks(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule.yaml"
    write_capsule(capsule, exit_code=3)
    result = runner.invoke(app, ["run", str(capsule), "--workspace", str(tmp_path), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "failed"


def test_schema_command_writes_schema(tmp_path: Path) -> None:
    output = tmp_path / "schema.json"
    result = runner.invoke(app, ["schema", "--output", str(output)])
    assert result.exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["title"] == "TaskCapsule"


def test_validate_invalid_capsule_returns_two(tmp_path: Path) -> None:
    capsule = tmp_path / "bad.yaml"
    capsule.write_text("id: bad\n", encoding="utf-8")
    result = runner.invoke(app, ["validate", str(capsule)])
    assert result.exit_code == 2
    assert "Invalid capsule" in result.stderr


def test_schema_command_prints_json() -> None:
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["title"] == "TaskCapsule"


def test_run_renders_table(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule.yaml"
    write_capsule(capsule)
    result = runner.invoke(app, ["run", str(capsule), "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    assert "E2H replay" in result.stdout
    assert "passed" in result.stdout


def test_run_missing_workspace_returns_two(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule.yaml"
    write_capsule(capsule)
    result = runner.invoke(
        app,
        ["run", str(capsule), "--workspace", str(tmp_path / "missing"), "--json"],
    )
    assert result.exit_code == 2
    assert "Unable to run capsule" in result.stderr
