from __future__ import annotations

from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.store_models import QueryView, StoreInfo


def _empty_info() -> StoreInfo:
    return StoreInfo(
        schema_version="2",
        sources=0,
        runs=0,
        checks=0,
        variant_summaries=0,
        failure_records=0,
    )


def test_memory_query_opens_only_private_snapshot_after_source_swap(
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
    calls: list[Path] = []

    def fake_query_store_with_info(
        path: Path,
        view: QueryView,
        *,
        limit: int = 100,
        read_only: bool = False,
    ) -> tuple[StoreInfo, list[dict[str, object]]]:
        assert view is QueryView.SOURCES
        assert read_only is True
        assert limit == 100
        database.rename(moved)
        replacement.rename(database)
        calls.append(path)
        assert path != database
        assert path.parent.name.startswith("e2h-mcp-store-")
        assert path.read_bytes() == b"inside database"
        assert Path(f"{path}.wal").read_bytes() == b"inside wal"
        return _empty_info(), [{"source_name": "inside"}]

    monkeypatch.setattr(mcp_server, "query_store_with_info", fake_query_store_with_info)
    service = E2HMCPService(MCPServerConfig(root=tmp_path, store=database))

    result = service.memory_query(QueryView.SOURCES)

    assert len(calls) == 1
    assert database.read_bytes() == b"outside database"
    assert result.rows == [{"source_name": "inside"}]


def test_memory_query_wraps_store_snapshot_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    database.write_bytes(b"inside")
    called = False

    def fail_snapshot(path: Path) -> object:
        assert path == database.resolve()
        raise mcp_server.StoreSnapshotError("store changed while snapshotting")

    def unexpected_query(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("DuckDB query should not be reached")

    monkeypatch.setattr(mcp_server, "stable_store_snapshot", fail_snapshot)
    monkeypatch.setattr(mcp_server, "query_store_with_info", unexpected_query)
    service = E2HMCPService(MCPServerConfig(root=tmp_path, store=database))

    with pytest.raises(MCPServiceError, match="store changed while snapshotting"):
        service.memory_query(QueryView.SOURCES)

    assert called is False
