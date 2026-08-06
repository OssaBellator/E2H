from __future__ import annotations

import json
from pathlib import Path

import typer
from typer.testing import CliRunner

from e2h.snapshot_cli import snapshot_app

app = typer.Typer()
app.add_typer(snapshot_app, name="snapshot")
runner = CliRunner()


def test_snapshot_cli_full_flow(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "result.json").write_text('{"ok": true}', encoding="utf-8")
    archive = tmp_path / "workspace.e2hsnap"
    restored = tmp_path / "restored"

    result = runner.invoke(app, ["snapshot", "create", str(root), str(archive)])
    assert result.exit_code == 0, result.output
    assert "Created" in result.output

    result = runner.invoke(app, ["snapshot", "verify", str(archive)])
    assert result.exit_code == 0, result.output
    assert "Valid" in result.output

    result = runner.invoke(app, ["snapshot", "inspect", str(archive), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["core"]["entries"][0]["path"] == "result.json"

    result = runner.invoke(
        app,
        ["snapshot", "reference", str(archive), "--locator", "cas://workspace"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["locator"] == "cas://workspace"

    result = runner.invoke(app, ["snapshot", "restore", str(archive), str(restored)])
    assert result.exit_code == 0, result.output
    assert (restored / "result.json").exists()


def test_snapshot_cli_reports_invalid_archive(tmp_path: Path) -> None:
    archive = tmp_path / "bad.e2hsnap"
    archive.write_text("bad", encoding="utf-8")
    result = runner.invoke(app, ["snapshot", "verify", str(archive)])
    assert result.exit_code == 2
    assert "Invalid snapshot" in result.output
