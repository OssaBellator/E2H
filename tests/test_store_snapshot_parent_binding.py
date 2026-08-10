from __future__ import annotations

from pathlib import Path

import pytest

import e2h.store_snapshot as store_snapshot
from e2h.store_snapshot import StoreSnapshotError, stable_store_snapshot

pytestmark = pytest.mark.skipif(
    not store_snapshot._DESCRIPTOR_BOUND_SUPPORTED,
    reason="descriptor-bound store snapshots are unavailable",
)


def test_snapshot_stays_bound_to_open_parent_after_path_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    parent = root / "stores"
    parent.mkdir()
    database = parent / "evidence.duckdb"
    database.write_bytes(b"inside")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / database.name).write_bytes(b"outside")
    moved_parent = tmp_path / "original-stores"
    original_copy = store_snapshot._copy_descriptor
    swapped = False

    def rebinding_copy(descriptor: int, destination: Path, expected_size: int) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            parent.rename(moved_parent)
            try:
                parent.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable: {exc}")
        original_copy(descriptor, destination, expected_size)

    monkeypatch.setattr(store_snapshot, "_copy_descriptor", rebinding_copy)

    with stable_store_snapshot(database) as private_database:
        assert private_database.read_bytes() == b"inside"

    assert swapped is True
    assert database.read_bytes() == b"outside"


def test_snapshot_fails_closed_when_descriptor_binding_is_unavailable(
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
