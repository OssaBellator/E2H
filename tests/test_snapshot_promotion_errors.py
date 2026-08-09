from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot_module
import e2h.snapshot_promotion as promotion
from e2h.snapshot import SnapshotError

pytestmark = pytest.mark.skipif(
    not snapshot_module._WRITE_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative snapshot write support",
)


def _open_directory(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    return descriptor, os.fstat(descriptor)


def test_parent_stability_wraps_restat_failure(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    output = parent / "output"
    descriptor, opened = _open_directory(parent)
    os.close(descriptor)

    with pytest.raises(SnapshotError, match="unable to restat snapshot output parent"):
        promotion._parent_must_be_stable(
            output,
            descriptor,
            opened,
            noun="snapshot output",
        )


def test_identity_cleanup_helpers_tolerate_unreadable_parent(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    descriptor, _ = _open_directory(parent)
    os.close(descriptor)

    promotion._remove_regular_file_by_identity_at(descriptor, (1, 1))
    promotion._remove_tree_by_identity_at(descriptor, (1, 1))


def test_regular_file_cleanup_skips_other_entries_and_handles_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "other.txt").write_bytes(b"other")
    target = parent / "target.txt"
    target.write_bytes(b"target")
    expected = target.stat(follow_symlinks=False)
    descriptor, _ = _open_directory(parent)
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == target.name and kwargs.get("dir_fd") == descriptor:
            raise OSError("injected open failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(promotion.os, "open", failing_open)
    try:
        promotion._remove_regular_file_by_identity_at(
            descriptor,
            (expected.st_dev, expected.st_ino),
        )
    finally:
        os.close(descriptor)

    assert target.read_bytes() == b"target"
    assert (parent / "other.txt").read_bytes() == b"other"


def test_bound_directory_rejects_wrong_expected_identity(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    first = parent / "first"
    second = parent / "second"
    first.mkdir()
    second.mkdir()
    descriptor, _ = _open_directory(parent)
    try:
        with pytest.raises(OSError, match="identity changed"):
            promotion._open_bound_directory(
                descriptor,
                first.name,
                second.stat(follow_symlinks=False),
            )
    finally:
        os.close(descriptor)


def test_tree_cleanup_handles_missing_mismatch_non_directory_and_nested_tree(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    file_entry = parent / "file.txt"
    file_entry.write_bytes(b"file")
    tree = parent / "tree"
    nested = tree / "nested"
    nested.mkdir(parents=True)
    (nested / "value.txt").write_text("inside\n", encoding="utf-8")
    descriptor, _ = _open_directory(parent)
    tree_info = tree.stat(follow_symlinks=False)
    try:
        promotion._remove_tree_at(descriptor, "missing")
        promotion._remove_tree_at(descriptor, tree.name, expected_identity=(0, 0))
        promotion._remove_tree_at(descriptor, file_entry.name)
        promotion._remove_tree_at(
            descriptor,
            tree.name,
            expected_identity=(tree_info.st_dev, tree_info.st_ino),
        )
    finally:
        os.close(descriptor)

    assert file_entry.read_bytes() == b"file"
    assert not tree.exists()


def test_snapshot_post_rename_stat_error_cleans_promoted_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    output = parent / "output"
    temporary = parent / ".temporary"
    temporary.write_bytes(b"snapshot")
    descriptor, opened = _open_directory(parent)
    original_stat_entry = promotion._stat_entry
    calls = 0

    def failing_stat(parent_descriptor: int, name: str) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected final stat failure")
        return original_stat_entry(parent_descriptor, name)

    monkeypatch.setattr(promotion, "_stat_entry", failing_stat)
    try:
        with pytest.raises(SnapshotError, match="unable to publish snapshot output"):
            promotion.promote_snapshot_file(output, descriptor, opened, temporary.name)
    finally:
        os.close(descriptor)

    assert not output.exists()


def test_restore_rename_and_post_rename_stat_errors_are_wrapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    destination = parent / "destination"
    first_staging = parent / ".first"
    first_staging.mkdir()
    descriptor, opened = _open_directory(parent)
    original_rename = os.rename

    def failing_rename(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(promotion.os, "rename", failing_rename)
    try:
        with pytest.raises(SnapshotError, match="unable to publish restore destination"):
            promotion.promote_restore_tree(destination, descriptor, opened, first_staging.name)
    finally:
        os.close(descriptor)

    assert first_staging.is_dir()

    second_staging = parent / ".second"
    second_staging.mkdir()
    descriptor, opened = _open_directory(parent)
    monkeypatch.setattr(promotion.os, "rename", original_rename)
    original_stat_entry = promotion._stat_entry
    calls = 0

    def failing_stat(parent_descriptor: int, name: str) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected final stat failure")
        return original_stat_entry(parent_descriptor, name)

    monkeypatch.setattr(promotion, "_stat_entry", failing_stat)
    try:
        with pytest.raises(SnapshotError, match="unable to publish restore destination"):
            promotion.promote_restore_tree(destination, descriptor, opened, second_staging.name)
    finally:
        os.close(descriptor)

    assert not destination.exists()
