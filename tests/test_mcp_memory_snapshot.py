"""Regression coverage for single-snapshot MCP memory evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import e2h.mcp_server as mcp_server
import e2h.store as store
from e2h.mcp_server import E2HMCPService, MCPServerConfig
from e2h.store_models import QueryView, StoreInfo


class _FakeReadConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.description: list[tuple[str]] | None = None
        self._rows: list[tuple[Any, ...]] = []
        self.closed = False

    def execute(self, sql: str) -> _FakeReadConnection:
        self.statements.append(sql)
        if sql in {"BEGIN TRANSACTION", "COMMIT", "ROLLBACK"}:
            self.description = None
            self._rows = []
        else:
            self.description = [("source_sha256",)]
            self._rows = [("a" * 64,)]
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def close(self) -> None:
        self.closed = True


def _empty_info() -> StoreInfo:
    return StoreInfo(
        schema_version="2",
        sources=0,
        runs=0,
        checks=0,
        variant_summaries=0,
        failure_records=0,
    )


def test_store_snapshot_uses_one_read_only_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeReadConnection()
    opened: list[bool] = []

    def fake_connection(database: Path, *, read_only: bool = False) -> _FakeReadConnection:
        del database
        opened.append(read_only)
        return connection

    monkeypatch.setattr(store, "_connection", fake_connection)
    monkeypatch.setattr(store, "_info", lambda current: _empty_info())

    info, rows = store.query_store_snapshot(
        tmp_path / "store.duckdb",
        QueryView.SOURCES,
        limit=5,
    )

    assert opened == [True]
    assert connection.statements[0] == "BEGIN TRANSACTION"
    assert connection.statements[-1] == "COMMIT"
    assert sum("LIMIT 5" in statement for statement in connection.statements) == 1
    assert connection.closed is True
    assert info.sources == 0
    assert rows == [{"source_sha256": "a" * 64}]


def test_mcp_memory_query_uses_single_store_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "store.duckdb"
    database.write_bytes(b"placeholder")
    calls: list[tuple[Path, QueryView, int]] = []

    def fake_snapshot(
        path: Path,
        view: QueryView,
        *,
        limit: int,
    ) -> tuple[StoreInfo, list[dict[str, Any]]]:
        calls.append((path, view, limit))
        return _empty_info(), [{"source_sha256": "a" * 64}]

    monkeypatch.setattr(mcp_server, "query_store_snapshot", fake_snapshot)
    service = E2HMCPService(
        MCPServerConfig(root=tmp_path, store=Path("store.duckdb"))
    )

    result = service.memory_query(QueryView.SOURCES, limit=5)

    assert calls == [(database.resolve(), QueryView.SOURCES, 5)]
    assert result.row_count == 1
    assert result.store_source_count == 0
    assert len(result.memory_sha256) == 64
