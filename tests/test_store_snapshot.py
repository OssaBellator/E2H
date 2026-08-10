from __future__ import annotations

from pathlib import Path

import pytest

import e2h.store_snapshot as store_snapshot
from e2h.store_snapshot import StoreSnapshotError, stable_store_snapshot

pytestmark = pytest.mark.skipif(
    not store_snapshot._DESCRIPTOR_BOUND_SUPPORTED,
    reason="descriptor-bound store snapshots are unavailable",
)


def test_stable_store_snapshot_copies_database_and_duckdb_sidecars(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    files = {
        database: b"database",
        Path(f"{database}.wal"): b"wal",
        Path(f"{database}.wal.checkpoint"): b"checkpoint",
        Path(f"{database}.wal.recovery"): b"recovery",
    }
    for path, content in files.items():
        path.write_bytes(content)

    private_parent: Path | None = None
    with stable_store_snapshot(database) as private_database:
        private_parent = private_database.parent
        assert private_database != database
        for source, content in files.items():
            private = private_database.parent / source.name
            assert private.read_bytes() == content

    assert private_parent is not None
    assert not private_parent.exists()


def test_stable_store_snapshot_rejects_main_file_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside.duckdb"
    outside.write_bytes(b"outside")
    database = tmp_path / "evidence.duckdb"
    try:
        database.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(StoreSnapshotError, match="must be a regular file"):
        with stable_store_snapshot(database):
            raise AssertionError("symlinked store should not be yielded")


def test_stable_store_snapshot_rejects_parent_symlink_rebinding(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    parent = root / "stores"
    parent.mkdir()
    database = parent / "evidence.duckdb"
    database.write_bytes(b"inside")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / database.name).write_bytes(b"outside")
    moved = tmp_path / "original-stores"
    parent.rename(moved)
    try:
        parent.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(StoreSnapshotError, match="unable to bind store parent"):
        with stable_store_snapshot(database):
            raise AssertionError("rebound store parent should not be yielded")


def test_stable_store_snapshot_rejects_file_replacement_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    database.write_bytes(b"inside")
    replacement = tmp_path / "replacement.duckdb"
    replacement.write_bytes(b"outside")
    moved = tmp_path / "original.duckdb"

    original_copy = store_snapshot._copy_descriptor
    swapped = False

    def swapping_copy(descriptor: int, destination: Path, expected_size: int) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            database.rename(moved)
            replacement.rename(database)
        original_copy(descriptor, destination, expected_size)

    monkeypatch.setattr(store_snapshot, "_copy_descriptor", swapping_copy)

    with pytest.raises(StoreSnapshotError, match="changed while snapshotting"):
        with stable_store_snapshot(database):
            raise AssertionError("replaced store should not be yielded")

    assert swapped is True


def test_stable_store_snapshot_rejects_new_sidecar_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "evidence.duckdb"
    database.write_bytes(b"database")
    wal = Path(f"{database}.wal")

    original_copy = store_snapshot._copy_descriptor
    created = False

    def creating_copy(descriptor: int, destination: Path, expected_size: int) -> None:
        nonlocal created
        if not created:
            created = True
            wal.write_bytes(b"new wal")
        original_copy(descriptor, destination, expected_size)

    monkeypatch.setattr(store_snapshot, "_copy_descriptor", creating_copy)

    with pytest.raises(StoreSnapshotError, match="changed while snapshotting"):
        with stable_store_snapshot(database):
            raise AssertionError("changed sidecar set should not be yielded")

    assert created is True
