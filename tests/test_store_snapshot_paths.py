from __future__ import annotations

from pathlib import Path

import pytest

import e2h.store_snapshot as store_snapshot
from e2h.store_snapshot import StoreSnapshotError, stable_store_snapshot


def test_snapshot_rejects_relative_final_segment(tmp_path: Path) -> None:
    database = tmp_path / "store" / ".."

    with pytest.raises(StoreSnapshotError, match="must name a file without relative segments"):
        with stable_store_snapshot(database):
            raise AssertionError("relative final entry should not be opened")


@pytest.mark.skipif(
    not store_snapshot._DESCRIPTOR_BOUND_SUPPORTED,
    reason="descriptor-bound store snapshots are unavailable",
)
def test_snapshot_rejects_relative_parent_segment(tmp_path: Path) -> None:
    database = tmp_path / "store" / ".." / "evidence.duckdb"

    with pytest.raises(StoreSnapshotError, match="must not contain relative path segments"):
        with stable_store_snapshot(database):
            raise AssertionError("relative parent entry should not be opened")
