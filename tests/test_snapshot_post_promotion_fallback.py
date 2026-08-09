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


def test_snapshot_output_fallback_rejects_final_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    output = tmp_path / "workspace.e2hsnap"
    replacement = tmp_path / "replacement.e2hsnap"
    replacement.write_bytes(b"attacker")
    moved = tmp_path / "published.e2hsnap"
    original_replace = os.replace
    swapped = False
    monkeypatch.setattr(snapshot_module, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_replace(src, dst, *args, **kwargs)
        if not swapped and Path(dst) == output:
            swapped = True
            original_replace(output, moved)
            original_replace(replacement, output)

    monkeypatch.setattr(os, "replace", swapping_replace)

    with pytest.raises(SnapshotError, match="output changed during publication"):
        create_snapshot(root, output)

    assert swapped is True
    assert output.read_bytes() == b"attacker"
    assert moved.is_file()


def test_snapshot_output_fallback_rejects_parent_replacement(
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
    original_replace = os.replace
    original_rename = os.rename
    swapped = False
    monkeypatch.setattr(snapshot_module, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_replace(src, dst, *args, **kwargs)
        if not swapped and Path(dst) == output:
            swapped = True
            original_rename(parent, moved)
            original_rename(replacement, parent)

    monkeypatch.setattr(os, "replace", swapping_replace)

    with pytest.raises(SnapshotError):
        create_snapshot(root, output)

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / output.name).exists()
    assert (moved / output.name).is_file()


def test_restore_fallback_rejects_final_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)
    destination = tmp_path / "restored"
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("attacker\n", encoding="utf-8")
    moved = tmp_path / "published"
    original_replace = os.replace
    swapped = False
    monkeypatch.setattr(snapshot_module, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_replace(src, dst, *args, **kwargs)
        if not swapped and Path(dst) == destination:
            swapped = True
            original_replace(destination, moved)
            original_replace(replacement, destination)

    monkeypatch.setattr(os, "replace", swapping_replace)

    with pytest.raises(SnapshotError, match="destination changed during publication"):
        restore_snapshot(archive, destination)

    assert swapped is True
    assert (destination / "marker.txt").read_text(encoding="utf-8") == "attacker\n"
    assert (moved / "value.txt").read_text(encoding="utf-8") == "inside\n"


def test_restore_fallback_rejects_parent_replacement(
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
    original_replace = os.replace
    original_rename = os.rename
    swapped = False
    monkeypatch.setattr(snapshot_module, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        original_replace(src, dst, *args, **kwargs)
        if not swapped and Path(dst) == destination:
            swapped = True
            original_rename(parent, moved)
            original_rename(replacement, parent)

    monkeypatch.setattr(os, "replace", swapping_replace)

    with pytest.raises(SnapshotError):
        restore_snapshot(archive, destination)

    assert swapped is True
    assert (parent / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert not (parent / destination.name).exists()
    assert (moved / destination.name / "value.txt").read_text(encoding="utf-8") == "inside\n"
