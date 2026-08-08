from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot_module
from e2h.snapshot import SnapshotError, create_snapshot, restore_snapshot, verify_snapshot


def _archive(tmp_path: Path, *, name: str = "workspace.e2hsnap", value: str = "inside") -> Path:
    root = tmp_path / f"root-{name}"
    root.mkdir()
    (root / "value.txt").write_text(value, encoding="utf-8")
    archive = tmp_path / name
    create_snapshot(root, archive)
    return archive


def test_verify_snapshot_rejects_final_symlink(tmp_path: Path) -> None:
    target = _archive(tmp_path, name="target.e2hsnap")
    link = tmp_path / "link.e2hsnap"
    link.symlink_to(target)

    with pytest.raises(SnapshotError, match="snapshot archive must be a regular file"):
        verify_snapshot(link)


def test_verify_snapshot_rejects_archive_swap_to_outside_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path)
    outside = _archive(tmp_path, name="outside.e2hsnap", value="outside")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == archive.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            archive.unlink()
            archive.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(SnapshotError, match="snapshot archive"):
        verify_snapshot(archive)

    assert swapped is True


def test_restore_rejects_archive_replacement_after_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path)
    outside = _archive(tmp_path, name="outside.e2hsnap", value="outside")
    moved = tmp_path / "verified-original.e2hsnap"
    destination = tmp_path / "restored"
    original_verify = snapshot_module._verify_snapshot_archive
    swapped = False

    def mutating_verify(*args: Any, **kwargs: Any):
        nonlocal swapped
        manifest = original_verify(*args, **kwargs)
        archive.rename(moved)
        archive.symlink_to(outside)
        swapped = True
        return manifest

    monkeypatch.setattr(snapshot_module, "_verify_snapshot_archive", mutating_verify)

    with pytest.raises(SnapshotError, match="snapshot archive changed while reading"):
        restore_snapshot(archive, destination)

    assert swapped is True
    assert not destination.exists()
