"""Regression coverage for controlled capture CLI publication failures."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import e2h.capture_cli as capture_cli

app = typer.Typer()
app.add_typer(capture_cli.capture_app, name="capture")
runner = CliRunner()


def test_capture_schema_normalizes_atomic_output_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(path: Path, payload: str) -> None:
        del path, payload
        raise OSError("publication refused")

    monkeypatch.setattr(capture_cli, "write_json_atomic", fail_write)

    result = runner.invoke(
        app,
        ["capture", "schema", "--output", str(tmp_path / "capture.schema.json")],
    )

    assert result.exit_code == 2
    assert "Unable to write capture schema" in result.output
    assert "publication refused" in result.output
