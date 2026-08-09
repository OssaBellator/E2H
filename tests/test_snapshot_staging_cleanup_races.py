from __future__ import annotations

import os
from pathlib import Path

import pytest

import e2h.snapshot as snapshot

pytestmark = pytest.mark.skipif(
    not snapshot._WRITE_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative snapshot write support",
)


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def test_staging_cleanup_preserves_substituted_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    staging = parent / "staging"
    staging.mkdir()
    child = staging / "value.txt"
    child.write_text("owned\n", encoding="utf-8")
    moved = tmp_path / "moved-owned.txt"
    replacement = tmp_path / "replacement.txt"
    replacement.write_text("replacement\n", encoding="utf-8")
    original_stat = os.stat
    swapped = False

    def swapping_stat(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal swapped
        info = original_stat(path, *args, **kwargs)
        if not swapped and path == child.name and kwargs.get("dir_fd") is not None:
            swapped = True
            child.rename(moved)
            replacement.rename(child)
        return info

    monkeypatch.setattr(snapshot.os, "stat", swapping_stat)
    descriptor = _open_directory(parent)
    try:
        snapshot._remove_restore_tree_at(descriptor, staging.name, staging)
    finally:
        os.close(descriptor)

    assert swapped is True
    assert child.read_text(encoding="utf-8") == "replacement\n"
    assert moved.read_text(encoding="utf-8") == "owned\n"
