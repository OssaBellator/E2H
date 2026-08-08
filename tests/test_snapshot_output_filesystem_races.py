from __future__ import annotations

import os
from pathlib import Path

import pytest

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
