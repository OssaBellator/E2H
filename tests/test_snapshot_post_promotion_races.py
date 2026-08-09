from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot_module
from e2h.snapshot import SnapshotError, create_snapshot, restore_snapshot

pytestmark = pytest.mark.skipif(
    not snapshot_module._WRITE_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative snapshot write support",
)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "child.txt").write_text("child\n", encoding="utf-8")
    return root


def test_create_snapshot_rejects_parent_swap_during_final_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "workspace.e2hsnap"
    moved = tmp_path / "original-output-parent"
    replacement = tmp_path / "replacement-output-parent"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if (
            not swapped
            and kwargs.get("src_dir_fd") is not None
            and kwargs.get("dst_dir_fd") is not None
            and str(dst) == output.name
        ):
            swapped = True
            original_rename(parent, moved)
            original_rename(replacement, parent)

    monkeypatch.setattr(os, "rename", swapping_rename)

    with pytest.raises(SnapshotError, match="parent changed during publication"):
        create_snapshot(root, output)

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / output.name).exists()
    assert not (moved / output.name).exists()


def test_create_snapshot_rejects_final_replacement_after_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "workspace.e2hsnap"
    replacement = parent / "replacement.e2hsnap"
    replacement.write_bytes(b"attacker")
    moved = parent / "published.e2hsnap"
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if (
            not swapped
            and kwargs.get("src_dir_fd") is not None
            and kwargs.get("dst_dir_fd") is not None
            and str(dst) == output.name
        ):
            swapped = True
            original_rename(output, moved)
            original_rename(replacement, output)

    monkeypatch.setattr(os, "rename", swapping_rename)

    with pytest.raises(SnapshotError, match="output changed during publication"):
        create_snapshot(root, output)

    assert swapped is True
    assert output.read_bytes() == b"attacker"
    assert not moved.exists()


def test_restore_snapshot_rejects_parent_swap_during_final_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    destination = parent / "restored"
    moved = tmp_path / "original-restore-parent"
    replacement = tmp_path / "replacement-restore-parent"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if (
            not swapped
            and kwargs.get("src_dir_fd") is not None
            and kwargs.get("dst_dir_fd") is not None
            and str(dst) == destination.name
        ):
            swapped = True
            original_rename(parent, moved)
            original_rename(replacement, parent)

    monkeypatch.setattr(os, "rename", swapping_rename)

    with pytest.raises(SnapshotError, match="parent changed during publication"):
        restore_snapshot(archive, destination)

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / destination.name).exists()
    assert not (moved / destination.name).exists()


def test_restore_snapshot_rejects_final_replacement_after_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    destination = parent / "restored"
    replacement = parent / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("attacker\n", encoding="utf-8")
    moved = parent / "published"
    original_rename = os.rename
    swapped = False

    def swapping_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_rename(src, dst, *args, **kwargs)
        if (
            not swapped
            and kwargs.get("src_dir_fd") is not None
            and kwargs.get("dst_dir_fd") is not None
            and str(dst) == destination.name
        ):
            swapped = True
            original_rename(destination, moved)
            original_rename(replacement, destination)

    monkeypatch.setattr(os, "rename", swapping_rename)

    with pytest.raises(SnapshotError, match="destination changed during publication"):
        restore_snapshot(archive, destination)

    assert swapped is True
    assert (destination / "marker.txt").read_text(encoding="utf-8") == "attacker\n"
    assert not moved.exists()


def test_restore_snapshot_atomically_replaces_existing_empty_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "workspace.e2hsnap"
    manifest = create_snapshot(root, archive)
    destination = tmp_path / "restored"
    destination.mkdir()
    before = destination.stat(follow_symlinks=False)
    original_rmdir = os.rmdir

    def guarded_rmdir(path: Any, *args: Any, **kwargs: Any) -> None:
        if str(path) == destination.name or Path(path) == destination:
            raise AssertionError("restore removed destination before promotion")
        original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", guarded_rmdir)

    restored = restore_snapshot(archive, destination)

    after = destination.stat(follow_symlinks=False)
    assert restored.snapshot_id == manifest.snapshot_id
    assert (destination / "value.txt").read_text(encoding="utf-8") == "inside\n"
    assert (destination / "nested" / "child.txt").read_text(encoding="utf-8") == "child\n"
    assert (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
