from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot
import e2h.snapshot_promotion as promotion
from e2h.snapshot import (
    SnapshotError,
    archive_sha256,
    create_snapshot,
    restore_snapshot,
    snapshot_reference,
    verify_snapshot,
)


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "value.txt").write_text("value\n", encoding="utf-8")
    return root


def _aliased_parent(tmp_path: Path, prefix: str) -> tuple[Path, Path, Path]:
    original = tmp_path / f"{prefix}-original"
    original.mkdir()
    replacement = tmp_path / f"{prefix}-replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    alias = tmp_path / f"{prefix}-visible"
    alias.symlink_to(original, target_is_directory=True)
    return alias, original, replacement


def _retarget(alias: Path, replacement: Path) -> None:
    alias.unlink()
    alias.symlink_to(replacement, target_is_directory=True)


@pytest.mark.skipif(
    not snapshot._WRITE_DIR_FD_SUPPORTED,
    reason="descriptor-relative snapshot publication is unavailable",
)
def test_create_rejects_parent_alias_retarget_after_descriptor_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    alias, original, replacement = _aliased_parent(tmp_path, "create")
    output = alias / "snapshot.e2hsnap"
    original_rename = promotion.os.rename
    state = {"swapped": False}

    def swapping_rename(*args: Any, **kwargs: Any) -> None:
        original_rename(*args, **kwargs)
        if not state["swapped"]:
            state["swapped"] = True
            _retarget(alias, replacement)

    monkeypatch.setattr(promotion.os, "rename", swapping_rename)

    with pytest.raises(SnapshotError, match="snapshot output parent changed"):
        create_snapshot(root, output)

    assert state["swapped"] is True
    assert not (original / output.name).exists()
    assert not (replacement / output.name).exists()
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"


@pytest.mark.skipif(
    not snapshot._CLEANUP_DIR_FD_SUPPORTED,
    reason="identity-bound snapshot cleanup is unavailable",
)
def test_create_fallback_rejects_parent_alias_retarget_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    alias, original, replacement = _aliased_parent(tmp_path, "create-fallback")
    output = alias / "snapshot.e2hsnap"
    original_replace = snapshot.os.replace
    state = {"swapped": False}
    monkeypatch.setattr(snapshot, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_replace(*args: Any, **kwargs: Any) -> None:
        original_replace(*args, **kwargs)
        if not state["swapped"]:
            state["swapped"] = True
            _retarget(alias, replacement)

    monkeypatch.setattr(snapshot.os, "replace", swapping_replace)

    with pytest.raises(SnapshotError, match="snapshot output parent changed"):
        create_snapshot(root, output)

    assert state["swapped"] is True
    assert not (original / output.name).exists()
    assert not (replacement / output.name).exists()
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"


@pytest.mark.skipif(
    not snapshot._WRITE_DIR_FD_SUPPORTED,
    reason="descriptor-relative snapshot publication is unavailable",
)
def test_restore_rejects_parent_alias_retarget_after_descriptor_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "source.e2hsnap"
    create_snapshot(root, archive)
    alias, original, replacement = _aliased_parent(tmp_path, "restore")
    destination = alias / "restored"
    original_rename = promotion.os.rename
    state = {"swapped": False}

    def swapping_rename(*args: Any, **kwargs: Any) -> None:
        original_rename(*args, **kwargs)
        if not state["swapped"]:
            state["swapped"] = True
            _retarget(alias, replacement)

    monkeypatch.setattr(promotion.os, "rename", swapping_rename)

    with pytest.raises(SnapshotError, match="restore destination parent changed"):
        restore_snapshot(archive, destination)

    assert state["swapped"] is True
    assert not (original / destination.name).exists()
    assert not (replacement / destination.name).exists()
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"


@pytest.mark.skipif(
    not snapshot._CLEANUP_DIR_FD_SUPPORTED,
    reason="identity-bound restore cleanup is unavailable",
)
def test_restore_fallback_rejects_parent_alias_retarget_after_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "source.e2hsnap"
    create_snapshot(root, archive)
    alias, original, replacement = _aliased_parent(tmp_path, "restore-fallback")
    destination = alias / "restored"
    original_replace = snapshot.os.replace
    state = {"swapped": False}
    monkeypatch.setattr(snapshot, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_replace(*args: Any, **kwargs: Any) -> None:
        original_replace(*args, **kwargs)
        if not state["swapped"]:
            state["swapped"] = True
            _retarget(alias, replacement)

    monkeypatch.setattr(snapshot.os, "replace", swapping_replace)

    with pytest.raises(SnapshotError, match="restore destination parent changed"):
        restore_snapshot(archive, destination)

    assert state["swapped"] is True
    assert not (original / destination.name).exists()
    assert not (replacement / destination.name).exists()
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"


def _archive_alias(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    root = _workspace(tmp_path)
    alias, original, replacement = _aliased_parent(tmp_path, "archive")
    original_archive = original / "snapshot.e2hsnap"
    create_snapshot(root, original_archive)
    replacement_archive = replacement / original_archive.name
    replacement_archive.write_bytes(original_archive.read_bytes())
    return alias / original_archive.name, alias, original_archive, replacement, replacement_archive


def _archive_operation(name: str, path: Path, tmp_path: Path) -> None:
    if name == "verify":
        verify_snapshot(path)
    elif name == "hash":
        archive_sha256(path)
    elif name == "reference":
        snapshot_reference(path)
    elif name == "restore":
        restore_snapshot(path, tmp_path / "archive-restore")
    else:
        raise AssertionError(name)


_ARCHIVE_OPERATIONS = ("verify", "hash", "reference", "restore")


@pytest.mark.parametrize("operation", _ARCHIVE_OPERATIONS)
@pytest.mark.skipif(
    not snapshot._OPEN_SUPPORTS_DIR_FD or not snapshot._STAT_SUPPORTS_DIR_FD,
    reason="descriptor-relative snapshot archive reads are unavailable",
)
def test_descriptor_archive_read_rejects_parent_alias_retarget(
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested, alias, original_archive, replacement_parent, replacement_archive = _archive_alias(
        tmp_path
    )
    original_fdopen = os.fdopen
    state = {"swapped": False}

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["swapped"]:
            state["swapped"] = True
            _retarget(alias, replacement_parent)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(snapshot.os, "fdopen", swapping_fdopen)

    with pytest.raises(SnapshotError, match="snapshot archive parent changed while reading"):
        _archive_operation(operation, requested, tmp_path)

    assert state["swapped"] is True
    assert requested.resolve() == replacement_archive
    assert original_archive.read_bytes() == replacement_archive.read_bytes()


@pytest.mark.parametrize("operation", _ARCHIVE_OPERATIONS)
def test_fallback_archive_read_rejects_parent_alias_retarget(
    operation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested, alias, original_archive, replacement_parent, replacement_archive = _archive_alias(
        tmp_path
    )
    original_fdopen = os.fdopen
    state = {"swapped": False}
    monkeypatch.setattr(snapshot, "_OPEN_SUPPORTS_DIR_FD", False)
    monkeypatch.setattr(snapshot, "_STAT_SUPPORTS_DIR_FD", False)
    monkeypatch.setattr(snapshot, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["swapped"]:
            state["swapped"] = True
            _retarget(alias, replacement_parent)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(snapshot.os, "fdopen", swapping_fdopen)

    with pytest.raises(SnapshotError, match="snapshot archive parent changed while reading"):
        _archive_operation(operation, requested, tmp_path)

    assert state["swapped"] is True
    assert requested.resolve() == replacement_archive
    assert original_archive.read_bytes() == replacement_archive.read_bytes()
