"""Regression coverage for controlled genome CLI artifact-publication failures."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import e2h.genome_cli as genome_cli

app = typer.Typer()
app.add_typer(genome_cli.genome_app, name="genome")
runner = CliRunner()


def test_genome_schema_normalizes_atomic_output_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(path: Path, payload: str) -> None:
        del path, payload
        raise OSError("publication refused")

    monkeypatch.setattr(genome_cli, "write_json_atomic", fail_write)

    result = runner.invoke(
        app,
        ["genome", "schema", "--output", str(tmp_path / "genome.schema.json")],
    )

    assert result.exit_code == 2
    assert "Unable to write genome output" in result.output
    assert "publication refused" in result.output
