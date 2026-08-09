from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot
import e2h.snapshot_promotion as promotion
from e2h.snapshot import SnapshotError, create_snapshot, restore_snapshot


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")
    return root


def _swap_file(
    temporary: Path,
    moved: Path,
    replacement: Path,
) -> None:
    temporary.rename(moved)
    replacement.rename(temporary)


def _swap_tree(
    staging: Path,
    moved: Path,
    replacement: Path,
) -> None:
    staging.rename(moved)
    replacement.rename(staging)


@pytest.mark.skipif(
    not snapshot._WRITE_DIR_FD_SUPPORTED,
    reason="descriptor-relative snapshot publication is unavailable",
)
def test_descriptor_snapshot_rejects_substituted_temporary_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "workspace.e2hsnap"
    outside = tmp_path / "outside"
    outside.mkdir()
    moved = outside / "moved-snapshot.tmp"
    replacement = outside / "replacement.tmp"
    replacement.write_bytes(b"attacker")
    original_promote = promotion.promote_snapshot_file
    state: dict[str, Path | bool] = {"swapped": False}

    def swapping_promote(
        output_path: Path,
        parent_descriptor: int,
        parent_opened: os.stat_result,
        temporary_name: str,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        if not state["swapped"]:
            state["swapped"] = True
            temporary = output_path.parent / temporary_name
            state["temporary"] = temporary
            _swap_file(temporary, moved, replacement)
        original_promote(
            output_path,
            parent_descriptor,
            parent_opened,
            temporary_name,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(promotion, "promote_snapshot_file", swapping_promote)

    with pytest.raises(SnapshotError, match="temporary file changed before publication"):
        create_snapshot(root, output)

    temporary = state["temporary"]
    assert isinstance(temporary, Path)
    assert state["swapped"] is True
    assert temporary.read_bytes() == b"attacker"
    assert moved.is_file()
    assert not output.exists()


def test_fallback_snapshot_rejects_substituted_temporary_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    parent = tmp_path / "output-parent"
    parent.mkdir()
    output = parent / "workspace.e2hsnap"
    outside = tmp_path / "outside"
    outside.mkdir()
    moved = outside / "moved-snapshot.tmp"
    replacement = outside / "replacement.tmp"
    replacement.write_bytes(b"attacker")
    original_stable = snapshot._snapshot_write_parent_must_be_stable
    state: dict[str, Path | bool | int] = {"calls": 0, "swapped": False}
    monkeypatch.setattr(snapshot, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_stable(*args: Any, **kwargs: Any) -> None:
        original_stable(*args, **kwargs)
        state["calls"] = int(state["calls"]) + 1
        if state["calls"] == 2 and not state["swapped"]:
            temporary = next(parent.glob(f".{output.name}.*.tmp"))
            state["temporary"] = temporary
            state["swapped"] = True
            _swap_file(temporary, moved, replacement)

    monkeypatch.setattr(snapshot, "_snapshot_write_parent_must_be_stable", swapping_stable)

    with pytest.raises(SnapshotError, match="temporary file changed before publication"):
        create_snapshot(root, output)

    temporary = state["temporary"]
    assert isinstance(temporary, Path)
    assert state["swapped"] is True
    assert temporary.read_bytes() == b"attacker"
    assert moved.is_file()
    assert not output.exists()


@pytest.mark.skipif(
    not snapshot._WRITE_DIR_FD_SUPPORTED,
    reason="descriptor-relative snapshot publication is unavailable",
)
def test_descriptor_restore_rejects_substituted_staging_before_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    destination = parent / "restored"
    outside = tmp_path / "outside"
    outside.mkdir()
    moved = outside / "moved-staging"
    replacement = outside / "replacement-staging"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("attacker\n", encoding="utf-8")
    original_promote = promotion.promote_restore_tree
    state: dict[str, Path | bool] = {"swapped": False}

    def swapping_promote(
        destination_path: Path,
        parent_descriptor: int,
        parent_opened: os.stat_result,
        staging_name: str,
        *,
        expected_identity: tuple[int, int] | None = None,
    ) -> None:
        if not state["swapped"]:
            state["swapped"] = True
            staging = destination_path.parent / staging_name
            state["staging"] = staging
            _swap_tree(staging, moved, replacement)
        original_promote(
            destination_path,
            parent_descriptor,
            parent_opened,
            staging_name,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(promotion, "promote_restore_tree", swapping_promote)

    with pytest.raises(SnapshotError, match="staging directory changed before publication"):
        restore_snapshot(archive, destination)

    staging = state["staging"]
    assert isinstance(staging, Path)
    assert state["swapped"] is True
    assert (staging / "marker.txt").read_text(encoding="utf-8") == "attacker\n"
    assert (moved / "value.txt").read_text(encoding="utf-8") == "inside\n"
    assert not destination.exists()


def test_fallback_restore_rejects_substituted_staging_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(tmp_path)
    archive = tmp_path / "workspace.e2hsnap"
    create_snapshot(root, archive)
    parent = tmp_path / "restore-parent"
    parent.mkdir()
    destination = parent / "restored"
    outside = tmp_path / "outside"
    outside.mkdir()
    moved = outside / "moved-staging"
    replacement = outside / "replacement-staging"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("attacker\n", encoding="utf-8")
    original_check = snapshot._restore_destination_is_empty
    state: dict[str, Path | bool | int] = {"calls": 0, "swapped": False}
    monkeypatch.setattr(snapshot, "_WRITE_DIR_FD_SUPPORTED", False)

    def swapping_check(parent_descriptor: int, destination_path: Path) -> bool:
        result = original_check(parent_descriptor, destination_path)
        state["calls"] = int(state["calls"]) + 1
        if state["calls"] == 2 and not state["swapped"]:
            staging = next(parent.glob(f".{destination.name}.restore-*"))
            state["staging"] = staging
            state["swapped"] = True
            _swap_tree(staging, moved, replacement)
        return result

    monkeypatch.setattr(snapshot, "_restore_destination_is_empty", swapping_check)

    with pytest.raises(SnapshotError, match="staging directory changed before publication"):
        restore_snapshot(archive, destination)

    staging = state["staging"]
    assert isinstance(staging, Path)
    assert state["swapped"] is True
    assert (staging / "marker.txt").read_text(encoding="utf-8") == "attacker\n"
    assert (moved / "value.txt").read_text(encoding="utf-8") == "inside\n"
    assert not destination.exists()
