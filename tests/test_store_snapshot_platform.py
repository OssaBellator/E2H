from __future__ import annotations

from pathlib import Path

import pytest

import e2h.store_snapshot as store_snapshot
from e2h.store_snapshot import StoreSnapshotError, stable_store_snapshot


def test_snapshot_fails_closed_without_descriptor_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    database.write_bytes(b"inside")
    monkeypatch.setattr(store_snapshot, "_DESCRIPTOR_BOUND_SUPPORTED", False)

    with pytest.raises(
        StoreSnapshotError,
        match="platform does not support descriptor-bound DuckDB store snapshots",
    ):
        with stable_store_snapshot(database):
            raise AssertionError("unsupported platform should not yield a snapshot")
