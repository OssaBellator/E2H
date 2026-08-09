from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot_module
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


def test_snapshot_overwrite_fails_closed_when_hard_link_backup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "snapshot.e2hsnap"
    temporary = parent / ".snapshot.tmp"
    output.write_bytes(b"previous snapshot\n")
    temporary.write_bytes(b"replacement snapshot\n")
    descriptor, opened = _open_directory(parent)
    original_rename = os.rename
    original_supports_dir_fd = os.supports_dir_fd
    rename_calls = 0

    def failing_link(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated hard-link failure")

    def recording_rename(*args: Any, **kwargs: Any) -> None:
        nonlocal rename_calls
        rename_calls += 1
        original_rename(*args, **kwargs)

    monkeypatch.setattr(os, "link", failing_link)
    monkeypatch.setattr(os, "supports_dir_fd", {*original_supports_dir_fd, failing_link})
    monkeypatch.setattr(os, "rename", recording_rename)
    try:
        with pytest.raises(SnapshotError, match="simulated hard-link failure"):
            promote_snapshot_file(output, descriptor, opened, temporary.name)
    finally:
        os.close(descriptor)

    assert rename_calls == 0
    assert output.read_bytes() == b"previous snapshot\n"
    assert temporary.read_bytes() == b"replacement snapshot\n"
    assert not list(parent.glob(f".{output.name}.rollback-*.bak"))


def test_snapshot_backup_race_keeps_known_good_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "snapshot.e2hsnap"
    temporary = parent / ".snapshot.tmp"
    attacker = parent / "attacker.e2hsnap"
    previous = b"previous snapshot\n"
    attacker_bytes = b"attacker snapshot\n"
    output.write_bytes(previous)
    temporary.write_bytes(b"replacement snapshot\n")
    attacker.write_bytes(attacker_bytes)
    descriptor, opened = _open_directory(parent)
    original_link = os.link
    original_supports_dir_fd = os.supports_dir_fd
    swapped = False

    def swapping_link(*args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_link(*args, **kwargs)
        if not swapped:
            swapped = True
            output.unlink()
            attacker.rename(output)

    monkeypatch.setattr(os, "link", swapping_link)
    monkeypatch.setattr(os, "supports_dir_fd", {*original_supports_dir_fd, swapping_link})
    try:
        with pytest.raises(SnapshotError, match="changed while preserving"):
            promote_snapshot_file(output, descriptor, opened, temporary.name)
    finally:
        os.close(descriptor)

    assert swapped is True
    assert output.read_bytes() == attacker_bytes
    assert temporary.read_bytes() == b"replacement snapshot\n"
    backups = list(parent.glob(f".{output.name}.rollback-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == previous
