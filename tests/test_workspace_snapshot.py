from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

import e2h.workspace_snapshot as workspace_snapshot
from e2h.workspace_snapshot import WorkspaceSnapshotError, stable_workspace_snapshot


def test_workspace_snapshot_copies_nested_files_modes_and_safe_symlinks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "task"
    nested.mkdir(parents=True)
    shared = workspace / "shared.txt"
    shared.write_text("shared", encoding="utf-8")
    script = nested / "run.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    script.chmod(0o755)
    link = nested / "shared-link"
    try:
        link.symlink_to("../shared.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with stable_workspace_snapshot(workspace.resolve(), max_bytes=1024, max_entries=10) as private:
        assert (private / "shared.txt").read_text(encoding="utf-8") == "shared"
        assert (private / "task" / "run.sh").read_text(encoding="utf-8").startswith("#!/bin/sh")
        assert stat.S_IMODE((private / "task" / "run.sh").stat().st_mode) == 0o755
        assert (private / "task" / "shared-link").is_symlink()
        assert (private / "task" / "shared-link").read_text(encoding="utf-8") == "shared"


def test_workspace_snapshot_rejects_absolute_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "escape"
    try:
        link.symlink_to("/etc/passwd")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(WorkspaceSnapshotError, match="symlink target must be relative"):
        with stable_workspace_snapshot(workspace.resolve(), max_bytes=1024, max_entries=10):
            raise AssertionError("absolute symlink should not be copied")


def test_workspace_snapshot_rejects_relative_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "escape"
    try:
        link.symlink_to("../outside")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(WorkspaceSnapshotError, match="symlink escapes isolated root"):
        with stable_workspace_snapshot(workspace.resolve(), max_bytes=1024, max_entries=10):
            raise AssertionError("escaping symlink should not be copied")


def test_workspace_snapshot_enforces_byte_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.bin").write_bytes(b"12345")

    with pytest.raises(WorkspaceSnapshotError, match="max bytes \(4\)"):
        with stable_workspace_snapshot(workspace.resolve(), max_bytes=4, max_entries=10):
            raise AssertionError("oversized workspace should not be copied")


def test_workspace_snapshot_enforces_entry_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a").write_text("a", encoding="utf-8")
    (workspace / "b").write_text("b", encoding="utf-8")

    with pytest.raises(WorkspaceSnapshotError, match="max entries \(1\)"):
        with stable_workspace_snapshot(workspace.resolve(), max_bytes=1024, max_entries=1):
            raise AssertionError("workspace with too many entries should not be copied")


def test_workspace_snapshot_rejects_special_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fifo = workspace / "pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError) as exc:
        pytest.skip(f"FIFO creation unavailable: {exc}")

    with pytest.raises(WorkspaceSnapshotError, match="unsupported file type"):
        with stable_workspace_snapshot(workspace.resolve(), max_bytes=1024, max_entries=10):
            raise AssertionError("special file should not be copied")


def test_workspace_snapshot_rejects_file_replacement_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "data.txt"
    source.write_text("inside", encoding="utf-8")
    replacement = workspace / "replacement.txt"
    replacement.write_text("outside", encoding="utf-8")
    moved = workspace / "original.txt"
    original_open = workspace_snapshot.os.open
    swapped = False

    def swapping_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and kwargs.get("dir_fd") is not None and target == source.name:
            swapped = True
            source.rename(moved)
            replacement.rename(source)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(workspace_snapshot.os, "open", swapping_open)

    with pytest.raises(WorkspaceSnapshotError, match="changed while opening"):
        with stable_workspace_snapshot(workspace.resolve(), max_bytes=1024, max_entries=10):
            raise AssertionError("replaced source file should not be copied")

    assert swapped is True


def test_workspace_snapshot_stays_on_bound_root_after_path_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "marker.txt").write_text("outside", encoding="utf-8")
    moved = tmp_path / "original-workspace"
    original_copy_directory = workspace_snapshot._copy_directory
    swapped = False

    def rebinding_copy(*args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            workspace.rename(moved)
            try:
                workspace.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable: {exc}")
        original_copy_directory(*args, **kwargs)

    monkeypatch.setattr(workspace_snapshot, "_copy_directory", rebinding_copy)

    with stable_workspace_snapshot(workspace.resolve(), max_bytes=1024, max_entries=10) as private:
        assert (private / "marker.txt").read_text(encoding="utf-8") == "inside"

    assert swapped is True
    assert (workspace / "marker.txt").read_text(encoding="utf-8") == "outside"
