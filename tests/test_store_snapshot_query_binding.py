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
        return [("inside",)]


class _FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def execute(self, sql: str) -> Any:
        if sql.startswith("SELECT count(*)"):
            return _CountCursor()
        return _QueryCursor()

    def close(self) -> None:
        self.closed = True


def test_read_only_combined_query_opens_only_private_snapshot_after_source_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    database.write_bytes(b"inside database")
    wal = Path(f"{database}.wal")
    wal.write_bytes(b"inside wal")
    replacement = tmp_path / "replacement.duckdb"
    replacement.write_bytes(b"outside database")
    moved = tmp_path / "original.duckdb"
    connection = _FakeConnection()
    opened: list[Path] = []

    def fake_connection(path: Path, *, read_only: bool = False) -> _FakeConnection:
        assert read_only is True
        if not opened:
            database.rename(moved)
            replacement.rename(database)
        opened.append(path)
        assert path != database
        assert path.read_bytes() == b"inside database"
        assert Path(f"{path}.wal").read_bytes() == b"inside wal"
        return connection

    monkeypatch.setattr(store, "_connection", fake_connection)

    info, rows = store.query_store_with_info(
        database,
        QueryView.SOURCES,
        read_only=True,
    )

    assert len(opened) == 1
    assert opened[0].parent.name.startswith("e2h-mcp-store-")
    assert database.read_bytes() == b"outside database"
    assert info.sources == 1
    assert rows == [{"source_name": "inside"}]
    assert connection.closed is True
