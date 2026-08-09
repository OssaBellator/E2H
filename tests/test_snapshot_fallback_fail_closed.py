from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot_module
from e2h.snapshot import SnapshotError, create_snapshot, restore_snapshot


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")
    return root


def test_snapshot_fallback_backup_failure_preserves_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "snapshot.e2hsnap"
    previous = b"previous snapshot\n"
    output.write_bytes(previous)
    expected_identity = snapshot_module._inode_identity(output.stat(follow_symlinks=False))

    def failing_link(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated hard-link failure")

    monkeypatch.setattr(snapshot_module.os, "link", failing_link)

    with pytest.raises(SnapshotError, match="simulated hard-link failure"):
        snapshot_module._backup_snapshot_output_path(output, expected_identity)

    assert output.read_bytes() == previous
    assert not list(tmp_path.glob(f".{output.name}.rollback-*.bak"))


def test_restore_fallback_failure_preserves_existing_empty_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)
    destination = tmp_path / "restored"
    destination.mkdir()
    destination_identity = snapshot_module._inode_identity(
        destination.stat(follow_symlinks=False)
    )
    original_replace = os.replace
    monkeypatch.setattr(snapshot_module, "_WRITE_DIR_FD_SUPPORTED", False)

    def failing_replace(source: Any, target: Any, *args: Any, **kwargs: Any) -> None:
        if Path(target) == destination:
            raise OSError("simulated restore promotion failure")
        original_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(snapshot_module.os, "replace", failing_replace)

    with pytest.raises(SnapshotError, match="simulated restore promotion failure"):
        restore_snapshot(archive, destination)

    assert destination.is_dir()
    assert snapshot_module._inode_identity(
        destination.stat(follow_symlinks=False)
    ) == destination_identity
    assert not list(destination.iterdir())
