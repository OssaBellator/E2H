from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import e2h.store as store
from e2h.store_models import QueryView


class _CountCursor:
    def fetchall(self) -> list[tuple[int]]:
        return [(1,)]


class _QueryCursor:
    description = (("source_name",),)

    def fetchall(self) -> list[tuple[str]]:
        return [("artifact.json",)]


class _SchemaCursor:
    def __init__(self, rows: list[tuple[str]]) -> None:
        self.rows = rows
        self.fetchall_called = False

    def fetchall(self) -> list[tuple[str]]:
        self.fetchall_called = True
        return self.rows


class _SchemaConnection:
    def __init__(self, cursor: _SchemaCursor) -> None:
        self.cursor = cursor

    def execute(self, sql: str) -> _SchemaCursor:
        assert "schema_version" in sql
        return self.cursor


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.sql: list[str] = []

    def execute(self, sql: str) -> Any:
        self.sql.append(sql)
        if sql.startswith("SELECT count(*)"):
            return _CountCursor()
        return _QueryCursor()

    def close(self) -> None:
        self.closed = True


class _FailingQueryConnection(_FakeConnection):
    def execute(self, sql: str) -> Any:
        self.sql.append(sql)
        if sql.startswith("SELECT count(*)"):
            return _CountCursor()
        if " LIMIT " in sql:
            raise store.duckdb.Error("query failed")
        return _QueryCursor()


class _FailingQueryAndRollbackConnection(_FailingQueryConnection):
    def execute(self, sql: str) -> Any:
        if sql == "ROLLBACK":
            self.sql.append(sql)
            raise store.duckdb.Error("rollback failed")
        return super().execute(sql)


def test_schema_version_row_fully_consumes_result() -> None:
    cursor = _SchemaCursor([("2",)])
    connection = _SchemaConnection(cursor)

    assert store._schema_version_row(connection) == ("2",)
    assert cursor.fetchall_called is True


def test_query_store_with_info_uses_one_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    connection = _FakeConnection()
    calls: list[tuple[Path, bool]] = []

    def fake_connection(path: Path, *, read_only: bool = False) -> _FakeConnection:
        calls.append((path, read_only))
        return connection

    monkeypatch.setattr(store, "_connection", fake_connection)

    info, rows = store.query_store_with_info(
        database,
        QueryView.SOURCES,
        limit=7,
        read_only=True,
    )

    assert calls == [(database, True)]
    assert info.sources == 1
    assert info.runs == 1
    assert info.checks == 1
    assert info.variant_summaries == 1
    assert info.failure_records == 1
    assert rows == [{"source_name": "artifact.json"}]
    assert connection.closed is True
    assert connection.sql[0] == "BEGIN TRANSACTION"
    assert connection.sql[-1] == "COMMIT"
    assert any("LIMIT 7" in sql for sql in connection.sql[1:-1])


def test_query_store_with_info_rolls_back_failed_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    connection = _FailingQueryConnection()

    monkeypatch.setattr(store, "_connection", lambda *args, **kwargs: connection)

    with pytest.raises(store.StoreError, match="unable to query store: query failed"):
        store.query_store_with_info(
            database,
            QueryView.SOURCES,
            limit=7,
            read_only=True,
        )

    assert connection.sql[0] == "BEGIN TRANSACTION"
    assert connection.sql[-1] == "ROLLBACK"
    assert connection.closed is True


def test_query_store_with_info_preserves_primary_error_when_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    connection = _FailingQueryAndRollbackConnection()

    monkeypatch.setattr(store, "_connection", lambda *args, **kwargs: connection)

    with pytest.raises(store.StoreError, match="unable to query store: query failed"):
        store.query_store_with_info(
            database,
            QueryView.SOURCES,
            limit=7,
            read_only=True,
        )

    assert connection.sql[-1] == "ROLLBACK"
    assert connection.closed is True
