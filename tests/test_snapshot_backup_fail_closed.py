from __future__ import annotations

import os
from pathlib import Path

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

    def failing_link(*args: object, **kwargs: object) -> None:
        raise OSError("simulated hard-link failure")

    def recording_rename(*args: object, **kwargs: object) -> None:
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
