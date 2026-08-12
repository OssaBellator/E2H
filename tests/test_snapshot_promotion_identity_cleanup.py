from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot_module
import e2h.snapshot_promotion as snapshot_promotion
from e2h.snapshot import SnapshotError
from e2h.snapshot_promotion import promote_snapshot_file

pytestmark = pytest.mark.skipif(
    not snapshot_module._WRITE_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative snapshot write support",
)


def _open_directory(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    return descriptor, os.fstat(descriptor)


def test_failed_snapshot_promotion_removes_moved_new_identity_and_keeps_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "snapshot.e2hsnap"
    temporary = parent / ".snapshot.tmp"
    moved = parent / "moved-new-snapshot.e2hsnap"
    attacker = parent / "attacker.e2hsnap"
    previous = b"previous snapshot\n"
    replacement = b"replacement snapshot\n"
    attacker_bytes = b"attacker snapshot\n"
    output.write_bytes(previous)
    temporary.write_bytes(replacement)
    attacker.write_bytes(attacker_bytes)
    descriptor, opened = _open_directory(parent)
    original_rename = snapshot_promotion.os.rename
    swapped = False

    def swapping_rename(source: Any, destination: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(source, destination, *args, **kwargs)
        if not swapped and Path(source).name == temporary.name:
            swapped = True
            original_rename(output, moved)
            original_rename(attacker, output)

    monkeypatch.setattr(snapshot_promotion.os, "rename", swapping_rename)
    try:
        with pytest.raises(SnapshotError, match="output changed during publication"):
            promote_snapshot_file(output, descriptor, opened, temporary.name)
    finally:
        os.close(descriptor)

    assert swapped is True
    assert output.read_bytes() == attacker_bytes
    assert not moved.exists()
    backups = list(parent.glob(f".{output.name}.rollback-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == previous
