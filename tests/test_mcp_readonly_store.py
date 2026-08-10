"""Regression coverage for the MCP experiment-store read-only boundary."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.store import StoreError, initialize_store, query_store, store_info
from e2h.store_models import QueryView


def _set_store_schema_version(database: Path, version: str) -> None:
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "UPDATE store_metadata SET value = ? WHERE key = 'schema_version'",
            [version],
        )
    finally:
        connection.close()


def _store_schema_version(database: Path) -> str:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        row = connection.execute(
            "SELECT value FROM store_metadata WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def test_read_only_store_info_rejects_without_migrating_schema(tmp_path: Path) -> None:
    database = tmp_path / "store.duckdb"
    initialize_store(database)
    _set_store_schema_version(database, "1")

    with pytest.raises(StoreError, match="unsupported store schema version '1'"):
        store_info(database, read_only=True)

    assert _store_schema_version(database) == "1"


def test_read_only_store_query_preserves_current_schema(tmp_path: Path) -> None:
    database = tmp_path / "store.duckdb"
    initialize_store(database)

    info = store_info(database, read_only=True)
    rows = query_store(database, QueryView.SOURCES, limit=10, read_only=True)

    assert info.schema_version == "2"
    assert info.sources == 0
    assert rows == []
    assert _store_schema_version(database) == "2"


def test_mcp_memory_query_does_not_upgrade_store_schema(tmp_path: Path) -> None:
    database = tmp_path / "store.duckdb"
    initialize_store(database)
    _set_store_schema_version(database, "1")
    service = E2HMCPService(
        MCPServerConfig(root=tmp_path, store=Path("store.duckdb"))
    )

    with pytest.raises(MCPServiceError, match="unsupported store schema version '1'"):
        service.memory_query(QueryView.SOURCES, limit=10)

    assert _store_schema_version(database) == "1"
