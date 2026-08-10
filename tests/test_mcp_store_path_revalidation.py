from __future__ import annotations

from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.store_models import QueryView, StoreInfo


def test_memory_query_rejects_store_symlink_escape_after_startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    store = root / "evidence.duckdb"
    store.write_bytes(b"configured")
    outside = tmp_path / "outside.duckdb"
    outside.write_bytes(b"outside")
    service = E2HMCPService(MCPServerConfig(root=root, store=store))

    store.unlink()
    try:
        store.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    called = False

    def fake_query_store_with_info(
        path: Path,
        view: QueryView,
        *,
        limit: int = 100,
        read_only: bool = False,
    ) -> tuple[StoreInfo, list[dict[str, object]]]:
        nonlocal called
        called = True
        return (
            StoreInfo(
                schema_version="2",
                sources=0,
                runs=0,
                checks=0,
                variant_summaries=0,
                failure_records=0,
            ),
            [],
        )

    monkeypatch.setattr(mcp_server, "query_store_with_info", fake_query_store_with_info)

    with pytest.raises(MCPServiceError, match="memory store escapes the configured MCP root"):
        service.memory_query(QueryView.RUNS)

    assert called is False
