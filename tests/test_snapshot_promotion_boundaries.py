from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot_module
from e2h.snapshot import SnapshotError
from e2h.snapshot_promotion import promote_restore_tree, promote_snapshot_file

pytestmark = pytest.mark.skipif(
    not snapshot_module._WRITE_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative snapshot write support",
)


def _open_directory(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    return descriptor, os.fstat(descriptor)


def test_promote_snapshot_file_rejects_parent_swap_and_cleans_published_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "snapshot.e2hsnap"
    temporary = parent / ".snapshot.tmp"
    temporary.write_bytes(b"snapshot")
    moved = tmp_path / "original-parent"
    replacement = tmp_path / "replacement-parent"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    descriptor, opened = _open_directory(parent)
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if not swapped:
            swapped = True
            parent.rename(moved)
            replacement.rename(parent)

    monkeypatch.setattr(os, "rename", swapping_rename)
    try:
        with pytest.raises(SnapshotError, match="parent changed during publication"):
            promote_snapshot_file(output, descriptor, opened, temporary.name)
    finally:
        os.close(descriptor)

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / output.name).exists()
    assert not (moved / output.name).exists()


def test_promote_snapshot_file_rejects_final_replacement_without_deleting_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "snapshot.e2hsnap"
    temporary = parent / ".snapshot.tmp"
    temporary.write_bytes(b"snapshot")
    replacement = parent / "replacement.e2hsnap"
    replacement.write_bytes(b"attacker")
    moved = parent / "published.e2hsnap"
    descriptor, opened = _open_directory(parent)
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if not swapped:
            swapped = True
            output.rename(moved)
            replacement.rename(output)

    monkeypatch.setattr(os, "rename", swapping_rename)
    try:
        with pytest.raises(SnapshotError, match="output changed during publication"):
            promote_snapshot_file(output, descriptor, opened, temporary.name)
    finally:
        os.close(descriptor)

    assert swapped is True
    assert output.read_bytes() == b"attacker"
    assert not moved.exists()


def test_promote_restore_tree_rejects_parent_swap_and_cleans_nested_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    destination = parent / "restored"
    staging = parent / ".restore-staging"
    nested = staging / "nested"
    nested.mkdir(parents=True)
    (nested / "value.txt").write_text("inside\n", encoding="utf-8")
    moved = tmp_path / "original-parent"
    replacement = tmp_path / "replacement-parent"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    descriptor, opened = _open_directory(parent)
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if not swapped:
            swapped = True
            parent.rename(moved)
            replacement.rename(parent)

    monkeypatch.setattr(os, "rename", swapping_rename)
    try:
        with pytest.raises(SnapshotError, match="parent changed during publication"):
            promote_restore_tree(destination, descriptor, opened, staging.name)
    finally:
        os.close(descriptor)

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / destination.name).exists()
    assert not (moved / destination.name).exists()


def test_promote_restore_tree_rejects_final_replacement_without_deleting_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    destination = parent / "restored"
    staging = parent / ".restore-staging"
    staging.mkdir()
    (staging / "value.txt").write_text("inside\n", encoding="utf-8")
    replacement = parent / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("attacker\n", encoding="utf-8")
    moved = parent / "published"
    descriptor, opened = _open_directory(parent)
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if not swapped:
            swapped = True
            destination.rename(moved)
            replacement.rename(destination)

    monkeypatch.setattr(os, "rename", swapping_rename)
    try:
        with pytest.raises(SnapshotError, match="destination changed during publication"):
            promote_restore_tree(destination, descriptor, opened, staging.name)
    finally:
        os.close(descriptor)

    assert swapped is True
    assert (destination / "marker.txt").read_text(encoding="utf-8") == "attacker\n"
    assert not moved.exists()


def test_promote_restore_tree_atomically_replaces_existing_empty_destination(tmp_path: Path) -> None:
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    destination = parent / "restored"
    destination.mkdir()
    before = destination.stat(follow_symlinks=False)
    staging = parent / ".restore-staging"
    staging.mkdir()
    (staging / "value.txt").write_text("inside\n", encoding="utf-8")
    descriptor, opened = _open_directory(parent)
    try:
        promote_restore_tree(destination, descriptor, opened, staging.name)
    finally:
        os.close(descriptor)

    after = destination.stat(follow_symlinks=False)
    assert (destination / "value.txt").read_text(encoding="utf-8") == "inside\n"
    assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    assert not staging.exists()


def test_promote_snapshot_file_rejects_missing_and_nonregular_temporary(tmp_path: Path) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "snapshot.e2hsnap"
    descriptor, opened = _open_directory(parent)
    try:
        with pytest.raises(SnapshotError, match="unable to inspect snapshot temporary file"):
            promote_snapshot_file(output, descriptor, opened, ".missing.tmp")

        directory = parent / ".directory.tmp"
        directory.mkdir()
        with pytest.raises(SnapshotError, match="temporary file is not a regular file"):
            promote_snapshot_file(output, descriptor, opened, directory.name)
    finally:
        os.close(descriptor)


def test_promote_restore_tree_rejects_missing_and_non_directory_staging(tmp_path: Path) -> None:
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    destination = parent / "restored"
    descriptor, opened = _open_directory(parent)
    try:
        with pytest.raises(SnapshotError, match="unable to inspect restore staging directory"):
            promote_restore_tree(destination, descriptor, opened, ".missing")

        staging_file = parent / ".staging-file"
        staging_file.write_bytes(b"not-a-directory")
        with pytest.raises(SnapshotError, match="staging entry is not a directory"):
            promote_restore_tree(destination, descriptor, opened, staging_file.name)
    finally:
        os.close(descriptor)


def test_promotion_wraps_pre_rename_os_error_without_removing_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "snapshot.e2hsnap"
    temporary = parent / ".snapshot.tmp"
    temporary.write_bytes(b"snapshot")
    descriptor, opened = _open_directory(parent)

    def fail_rename(*args: Any, **kwargs: Any) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(os, "rename", fail_rename)
    try:
        with pytest.raises(SnapshotError, match="unable to publish snapshot output"):
            promote_snapshot_file(output, descriptor, opened, temporary.name)
    finally:
        os.close(descriptor)

    assert temporary.read_bytes() == b"snapshot"
    assert not output.exists()
