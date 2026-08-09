from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot_promotion as promotion


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def test_snapshot_identity_cleanup_removes_every_matching_hardlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    first.write_bytes(b"payload")
    second = tmp_path / "second"
    os.link(first, second)
    identity = promotion._inode_identity(first.stat(follow_symlinks=False))
    descriptor = _open_directory(tmp_path)
    original_listdir = os.listdir

    def second_first(target: Any) -> list[str]:
        names = list(original_listdir(target))
        if first.name in names and second.name in names:
            names.remove(first.name)
            names.remove(second.name)
            return [second.name, first.name, *names]
        return names

    monkeypatch.setattr(promotion.os, "listdir", second_first)
    try:
        promotion._remove_regular_file_by_identity_at(descriptor, identity)
    finally:
        os.close(descriptor)

    assert not first.exists()
    assert not second.exists()


def test_snapshot_tree_cleanup_survives_hardlink_scan_reordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    first = tree / "first"
    first.write_bytes(b"payload")
    second = tree / "second"
    os.link(first, second)
    tree_identity = promotion._inode_identity(tree.stat(follow_symlinks=False))
    parent_descriptor = _open_directory(tmp_path)
    original_listdir = os.listdir
    tree_list_calls = 0

    def reordered_listdir(target: Any) -> list[str]:
        nonlocal tree_list_calls
        names = list(original_listdir(target))
        if isinstance(target, int) and first.name in names and second.name in names:
            tree_list_calls += 1
            names.remove(first.name)
            names.remove(second.name)
            if tree_list_calls == 1:
                return [first.name, second.name, *names]
            return [second.name, first.name, *names]
        return names

    monkeypatch.setattr(promotion.os, "listdir", reordered_listdir)
    try:
        promotion._remove_tree_at(
            parent_descriptor,
            tree.name,
            expected_identity=tree_identity,
        )
    finally:
        os.close(parent_descriptor)

    assert tree_list_calls >= 2
    assert not tree.exists()
