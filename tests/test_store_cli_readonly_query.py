"""Regression coverage for the user-facing store-query no-migration contract."""

from __future__ import annotations

from pathlib import Path

import duckdb
import typer
from typer.testing import CliRunner

from e2h.store import initialize_store
from e2h.store_cli import store_app

app = typer.Typer()
app.add_typer(store_app, name="store")
runner = CliRunner()


def _schema_version(database: Path) -> str:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        row = connection.execute(
            "SELECT value FROM store_metadata WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def test_store_query_rejects_old_schema_without_migrating(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    initialize_store(database)
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "UPDATE store_metadata SET value = '1' WHERE key = 'schema_version'"
        )
    finally:
        connection.close()

    result = runner.invoke(app, ["store", "query", str(database), "runs", "--json"])

    assert result.exit_code == 2
    assert "unsupported store schema version '1'" in result.output
    assert _schema_version(database) == "1"
