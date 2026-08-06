from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import typer
from typer.testing import CliRunner

from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.store_cli import store_app

app = typer.Typer()
app.add_typer(store_app, name="store")
runner = CliRunner()
NOW = datetime(2026, 8, 6, tzinfo=UTC)


def write_run(path: Path) -> None:
    result = RunResult(
        capsule_id="cli-capsule",
        status=RunStatus.PASSED,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=1,
        checks=[
            CommandResult(
                id="contract",
                argv=["python", "-c", "pass"],
                cwd=".",
                status=CheckStatus.PASSED,
                exit_code=0,
                duration_seconds=0.1,
                stdout="ok\n",
            )
        ],
    )
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def test_store_cli_full_flow(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "run.json"
    parquet = tmp_path / "runs.parquet"
    write_run(artifact)

    result = runner.invoke(app, ["store", "init", str(database), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["schema_version"] == "2"

    result = runner.invoke(
        app,
        ["store", "ingest", str(database), str(artifact), "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["inserted"] is True

    result = runner.invoke(
        app,
        ["store", "query", str(database), "runs", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["capsule_id"] == "cli-capsule"

    result = runner.invoke(
        app,
        ["store", "info", str(database), "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["runs"] == 1

    result = runner.invoke(
        app,
        [
            "store",
            "export",
            str(database),
            str(parquet),
            "--view",
            "runs",
        ],
    )
    assert result.exit_code == 0, result.output
    with duckdb.connect() as connection:
        count = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(parquet)]
        ).fetchone()
    assert count == (1,)


def test_store_cli_human_readable_tables(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "run.json"
    write_run(artifact)

    result = runner.invoke(app, ["store", "init", str(database)])
    assert result.exit_code == 0, result.output
    assert "Ready" in result.output

    result = runner.invoke(app, ["store", "query", str(database), "runs"])
    assert result.exit_code == 0, result.output
    assert "No rows" in result.output

    result = runner.invoke(
        app,
        ["store", "ingest", str(database), str(artifact)],
    )
    assert result.exit_code == 0, result.output
    assert "E2H store ingestion" in result.output
    assert "yes" in result.output

    result = runner.invoke(app, ["store", "query", str(database), "runs"])
    assert result.exit_code == 0, result.output
    assert "┏" in result.output
    assert "1.0" in result.output

    result = runner.invoke(app, ["store", "info", str(database)])
    assert result.exit_code == 0, result.output
    assert "variant_summaries" in result.output
    assert "failure_records" in result.output


def test_store_cli_reports_invalid_artifact(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "bad.json"
    artifact.write_text("bad", encoding="utf-8")
    result = runner.invoke(
        app,
        ["store", "ingest", str(database), str(artifact)],
    )
    assert result.exit_code == 2
    assert "Unable to ingest artifact" in result.output


def test_store_cli_reports_invalid_database(tmp_path: Path) -> None:
    database = tmp_path / "invalid.duckdb"
    database.write_text("not-a-database", encoding="utf-8")
    output = tmp_path / "runs.parquet"

    result = runner.invoke(app, ["store", "query", str(database), "runs"])
    assert result.exit_code == 2
    assert "Unable to query store" in result.output

    result = runner.invoke(
        app,
        ["store", "export", str(database), str(output), "--view", "runs"],
    )
    assert result.exit_code == 2
    assert "Unable to export store" in result.output

    result = runner.invoke(app, ["store", "info", str(database)])
    assert result.exit_code == 2
    assert "Unable to inspect store" in result.output
