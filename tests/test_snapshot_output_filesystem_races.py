from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot_module
from e2h.snapshot import SnapshotError, create_snapshot, restore_snapshot, verify_snapshot


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")
    return root


def test_create_snapshot_rejects_existing_output_symlink(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    outside = tmp_path / "outside.e2hsnap"
    protected = b"outside-must-not-change\n"
    outside.write_bytes(protected)
    output = tmp_path / "workspace.e2hsnap"
    output.symlink_to(outside)

    with pytest.raises(SnapshotError, match="snapshot output must be a regular file"):
        create_snapshot(root, output)

    assert outside.read_bytes() == protected
    assert output.is_symlink()


def test_create_snapshot_does_not_follow_predictable_temporary_symlink(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    output = tmp_path / "workspace.e2hsnap"
    outside = tmp_path / "outside.txt"
    protected = b"outside-must-not-change\n"
    outside.write_bytes(protected)

    legacy_temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    legacy_temporary.symlink_to(outside)

    manifest = create_snapshot(root, output)

    assert outside.read_bytes() == protected
    assert legacy_temporary.is_symlink()
    assert output.is_file()
    assert not output.is_symlink()
    assert verify_snapshot(output).snapshot_id == manifest.snapshot_id


def test_restore_snapshot_rejects_existing_destination_symlink(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)

    outside = tmp_path / "outside-restore"
    outside.mkdir()
    destination = tmp_path / "restored"
    destination.symlink_to(outside, target_is_directory=True)

    with pytest.raises(SnapshotError, match="restore destination must not be a symbolic link"):
        restore_snapshot(archive, destination)

    assert destination.is_symlink()
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(
    not snapshot_module._WRITE_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative snapshot write support",
)
def test_create_snapshot_rejects_output_parent_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    output_parent = tmp_path / "output-parent"
    output_parent.mkdir()
    output = output_parent / "workspace.e2hsnap"
    moved = tmp_path / "original-output-parent"
    outside = tmp_path / "outside-output-parent"
    outside.mkdir()

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        name = Path(path).name
        if (
            name.startswith(f".{output.name}.")
            and name.endswith(".tmp")
            and kwargs.get("dir_fd") is not None
            and not swapped
        ):
            swapped = True
            output_parent.rename(moved)
            output_parent.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(SnapshotError, match="snapshot output parent changed while writing"):
        create_snapshot(root, output)

    assert swapped is True
    assert list(outside.iterdir()) == []
    assert not any(moved.glob(f".{output.name}.*.tmp"))


@pytest.mark.skipif(
    not snapshot_module._WRITE_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative snapshot write support",
)
def test_restore_snapshot_rejects_destination_parent_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)

    destination_parent = tmp_path / "restore-parent"
    destination_parent.mkdir()
    destination = destination_parent / "restored"
    moved = tmp_path / "original-restore-parent"
    outside = tmp_path / "outside-restore-parent"
    outside.mkdir()

    original_mkdir = os.mkdir
    swapped = False

    def swapping_mkdir(
        path: Any,
        mode: int = 0o777,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        nonlocal swapped
        name = Path(path).name
        if (
            name.startswith(f".{destination.name}.restore-")
            and kwargs.get("dir_fd") is not None
            and not swapped
        ):
            swapped = True
            destination_parent.rename(moved)
            destination_parent.symlink_to(outside, target_is_directory=True)
        original_mkdir(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", swapping_mkdir)

    with pytest.raises(SnapshotError, match="restore destination parent changed while writing"):
        restore_snapshot(archive, destination)

    assert swapped is True
    assert list(outside.iterdir()) == []
    assert not any(moved.glob(f".{destination.name}.restore-*"))
