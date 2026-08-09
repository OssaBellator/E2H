from __future__ import annotations

import os
from pathlib import Path

import pytest

import e2h.snapshot as snapshot_module
import e2h.snapshot_promotion as promotion

pytestmark = pytest.mark.skipif(
    not snapshot_module._WRITE_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative snapshot write support",
)


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _identity(path: Path) -> tuple[int, int]:
    info = path.stat(follow_symlinks=False)
    return (info.st_dev, info.st_ino)


def test_nested_cleanup_preserves_substituted_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    staging = parent / "staging"
    child = staging / "child"
    child.mkdir(parents=True)
    (child / "owned.txt").write_text("owned\n", encoding="utf-8")
    moved = tmp_path / "moved-owned"
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    original_stat = promotion._stat_entry
    swapped = False

    def swapping_stat(descriptor: int, name: str) -> os.stat_result:
        nonlocal swapped
        info = original_stat(descriptor, name)
        if not swapped and name == child.name:
            swapped = True
            child.rename(moved)
            replacement.rename(child)
        return info

    monkeypatch.setattr(promotion, "_stat_entry", swapping_stat)
    descriptor = _open_directory(parent)
    try:
        promotion._remove_tree_at(
            descriptor,
            staging.name,
            expected_identity=_identity(staging),
        )
    finally:
        os.close(descriptor)

    assert swapped is True
    assert (child / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert (moved / "owned.txt").read_text(encoding="utf-8") == "owned\n"


def test_nested_cleanup_preserves_substituted_regular_file(
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
    original_stat = promotion._stat_entry
    swapped = False

    def swapping_stat(descriptor: int, name: str) -> os.stat_result:
        nonlocal swapped
        info = original_stat(descriptor, name)
        if not swapped and name == child.name:
            swapped = True
            child.rename(moved)
            replacement.rename(child)
        return info

    monkeypatch.setattr(promotion, "_stat_entry", swapping_stat)
    descriptor = _open_directory(parent)
    try:
        promotion._remove_tree_at(
            descriptor,
            staging.name,
            expected_identity=_identity(staging),
        )
    finally:
        os.close(descriptor)

    assert swapped is True
    assert child.read_text(encoding="utf-8") == "replacement\n"
    assert moved.read_text(encoding="utf-8") == "owned\n"
