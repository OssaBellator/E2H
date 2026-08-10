from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

import e2h.store as store
from e2h.store_models import QueryView
from e2h.store_snapshot import StoreSnapshotError


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


def test_schema_version_row_fully_consumes_result() -> None:
    cursor = _SchemaCursor([("2",)])
    connection = _SchemaConnection(cursor)

    assert store._schema_version_row(connection) == ("2",)
    assert cursor.fetchall_called is True


def test_query_store_with_info_uses_private_snapshot_and_one_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    private_database = tmp_path / "private" / database.name
    connection = _FakeConnection()
    calls: list[tuple[Path, bool]] = []
    snapshots: list[Path] = []

    @contextmanager
    def fake_snapshot(path: Path) -> Iterator[Path]:
        snapshots.append(path)
        yield private_database

    def fake_connection(path: Path, *, read_only: bool = False) -> _FakeConnection:
        calls.append((path, read_only))
        return connection

    monkeypatch.setattr(store, "stable_store_snapshot", fake_snapshot)
    monkeypatch.setattr(store, "_connection", fake_connection)

    info, rows = store.query_store_with_info(
        database,
        QueryView.SOURCES,
        limit=7,
        read_only=True,
    )

    assert snapshots == [database]
    assert calls == [(private_database, True)]
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
    private_database = tmp_path / "private" / database.name
    connection = _FailingQueryConnection()

    @contextmanager
    def fake_snapshot(path: Path) -> Iterator[Path]:
        assert path == database
        yield private_database

    monkeypatch.setattr(store, "stable_store_snapshot", fake_snapshot)
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


def test_query_store_with_info_wraps_snapshot_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"

    @contextmanager
    def failing_snapshot(path: Path) -> Iterator[Path]:
        assert path == database
        raise StoreSnapshotError("store changed while snapshotting")
        yield path

    monkeypatch.setattr(store, "stable_store_snapshot", failing_snapshot)

    with pytest.raises(store.StoreError, match="store changed while snapshotting"):
        store.query_store_with_info(
            database,
            QueryView.SOURCES,
            read_only=True,
        )


def test_query_store_with_info_does_not_snapshot_writable_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    connection = _FakeConnection()

    @contextmanager
    def unexpected_snapshot(path: Path) -> Iterator[Path]:
        raise AssertionError(f"unexpected snapshot for {path}")
        yield path

    monkeypatch.setattr(store, "stable_store_snapshot", unexpected_snapshot)
    monkeypatch.setattr(store, "_connection", lambda *args, **kwargs: connection)

    store.query_store_with_info(
        database,
        QueryView.SOURCES,
        read_only=False,
    )

    assert connection.closed is True
