from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import e2h.store as store
from e2h.store_models import QueryView


class _CountCursor:
    def fetchone(self) -> tuple[int]:
        return (1,)


class _QueryCursor:
    description = (("source_name",),)

    def fetchall(self) -> list[tuple[str]]:
        return [("artifact.json",)]


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


def test_query_store_with_info_uses_one_connection(
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
    assert any("LIMIT 7" in sql for sql in connection.sql)
