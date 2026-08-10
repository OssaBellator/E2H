"""Regression coverage for controlled benchmark/release evidence publication failures."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import e2h.benchmark_cli as benchmark_cli
import e2h.benchmark_env_cli as benchmark_env_cli
import e2h.long_horizon_cli as long_horizon_cli
import e2h.release_cli as release_cli

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
            benchmark_cli,
            _app(benchmark_cli.benchmark_app, "benchmark"),
            ["benchmark", "schema", "--output", "schema.json"],
            "Unable to write benchmark output",
        ),
        (
            benchmark_env_cli,
            _app(benchmark_env_cli.environments_app, "environments"),
            ["environments", "schema", "--output", "schema.json"],
            "Unable to write benchmark environment output",
        ),
        (
            long_horizon_cli,
            _app(long_horizon_cli.long_horizon_app, "long-horizon"),
            ["long-horizon", "schema", "--output", "schema.json"],
            "Unable to write long-horizon output",
        ),
        (
            release_cli,
            _app(release_cli.release_app, "release"),
            ["release", "schema", "--output", "schema.json"],
            "Unable to write release output",
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
