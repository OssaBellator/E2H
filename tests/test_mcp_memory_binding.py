from __future__ import annotations

from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig
from e2h.store_models import QueryView, StoreInfo


def test_mcp_memory_query_uses_combined_store_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    database.write_bytes(b"placeholder")
    calls: list[tuple[Path, QueryView, int, bool]] = []

    def fake_query_store_with_info(
        path: Path,
        view: QueryView,
        *,
        limit: int = 100,
        read_only: bool = False,
    ) -> tuple[StoreInfo, list[dict[str, object]]]:
        calls.append((path, view, limit, read_only))
        return (
            StoreInfo(
                schema_version="2",
                sources=1,
                runs=1,
                checks=1,
                variant_summaries=0,
                failure_records=0,
            ),
            [{"capsule_id": "capsule"}],
        )

    monkeypatch.setattr(mcp_server, "query_store_with_info", fake_query_store_with_info)
    service = E2HMCPService(MCPServerConfig(root=tmp_path, store=database))

    result = service.memory_query(QueryView.RUNS, limit=4)

    assert calls == [(database.resolve(), QueryView.RUNS, 4, True)]
    assert result.store_source_count == 1
    assert result.row_count == 1
    assert result.rows == [{"capsule_id": "capsule"}]
