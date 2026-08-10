"""Regression coverage for controlled typed-artifact CLI publication failures."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import e2h.optimizer_cli as optimizer_cli
import e2h.partition_cli as partition_cli
import e2h.promotion_cli as promotion_cli
import e2h.variant_cli as variant_cli

runner = CliRunner()


def _fail_write(path: Path, payload: str) -> None:
    del path, payload
    raise OSError("publication refused")


def _app(command: typer.Typer, name: str) -> typer.Typer:
    app = typer.Typer()
    app.add_typer(command, name=name)
    return app


@pytest.mark.parametrize(
    ("module", "app", "argv", "message"),
    [
        (
            optimizer_cli,
            _app(optimizer_cli.optimizer_app, "optimizer"),
            ["optimizer", "schema", "--output", "schema.json"],
            "Unable to write optimizer output",
        ),
        (
            variant_cli,
            _app(variant_cli.variant_app, "variant"),
            ["variant", "schema", "--output", "schema.json"],
            "Unable to write variant output",
        ),
        (
            partition_cli,
            _app(partition_cli.partition_app, "partition"),
            ["partition", "schema", "--output", "schema.json"],
            "Unable to write partition output",
        ),
        (
            promotion_cli,
            _app(promotion_cli.promotion_app, "promotion"),
            ["promotion", "schema", "--output", "schema.json"],
            "Unable to write promotion output",
        ),
    ],
)
def test_schema_commands_normalize_atomic_output_errors(
    module: object,
    app: typer.Typer,
    argv: list[str],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "write_json_atomic", _fail_write)

    result = runner.invoke(app, argv)

    assert result.exit_code == 2
    assert message in result.output
    assert "publication refused" in result.output
