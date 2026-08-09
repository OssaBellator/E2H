from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import e2h.store as store_module
from e2h.store import StoreError, export_parquet, ingest_artifact, initialize_store
from e2h.store_models import QueryView


def test_store_rejects_nul_database_path_before_duckdb_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected_connect(*args: Any, **kwargs: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("duckdb.connect must not be called")

    monkeypatch.setattr(store_module.duckdb, "connect", unexpected_connect)

    with pytest.raises(StoreError, match="store database path must not contain NUL"):
        initialize_store(Path("bad\x00store.duckdb"))

    assert called is False


def test_ingest_rejects_nul_artifact_path_before_reading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected_read(path: Path) -> Any:
        nonlocal called
        called = True
        raise AssertionError(f"must not read {path}")

    monkeypatch.setattr(store_module, "read_artifact", unexpected_read)

    with pytest.raises(StoreError, match="artifact path must not contain NUL"):
        ingest_artifact(tmp_path / "store.duckdb", Path("bad\x00artifact.json"))

    assert called is False


def test_export_rejects_nul_output_before_opening_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def unexpected_connection(path: Path) -> Any:
        nonlocal called
        called = True
        raise AssertionError(f"must not open {path}")

    monkeypatch.setattr(store_module, "_connection", unexpected_connection)

    with pytest.raises(StoreError, match="Parquet output path must not contain NUL"):
        export_parquet(
            tmp_path / "store.duckdb",
            Path("bad\x00output.parquet"),
            QueryView.RUNS,
        )

    assert called is False


def test_store_wraps_parent_creation_failure(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(StoreError, match="unable to prepare experiment store"):
        initialize_store(blocker / "store.duckdb")


def test_store_closes_partial_connection_on_schema_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    class BrokenConnection:
        def execute(self, sql: str, parameters: Any = None) -> Any:
            del sql, parameters
            raise store_module.duckdb.Error("schema failure")

        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(store_module.duckdb, "connect", lambda path: BrokenConnection())

    with pytest.raises(StoreError, match="unable to open experiment store"):
        initialize_store(tmp_path / "store.duckdb")

    assert closed is True
