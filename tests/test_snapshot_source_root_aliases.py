from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot_source as snapshot_source
from e2h.snapshot import SnapshotError, create_snapshot


def _aliased_root(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    original_parent = tmp_path / "original-parent"
    original_root = original_parent / "workspace"
    original_root.mkdir(parents=True)
    (original_root / "value.txt").write_text("original\n", encoding="utf-8")

    replacement_parent = tmp_path / "replacement-parent"
    replacement_root = replacement_parent / "workspace"
    replacement_root.mkdir(parents=True)
    (replacement_root / "value.txt").write_text("replacement\n", encoding="utf-8")

    alias = tmp_path / "visible-parent"
    alias.symlink_to(original_parent, target_is_directory=True)
    return alias / "workspace", alias, original_root, replacement_parent, replacement_root


def _retarget(alias: Path, replacement_parent: Path) -> None:
    alias.unlink()
    alias.symlink_to(replacement_parent, target_is_directory=True)


def _retarget_on_read(
    alias: Path,
    replacement_parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bool]:
    original_fdopen = os.fdopen
    state = {"swapped": False}

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["swapped"]:
            state["swapped"] = True
            _retarget(alias, replacement_parent)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(snapshot_source.os, "fdopen", swapping_fdopen)
    return state


def _assert_trees_unchanged(original_root: Path, replacement_root: Path) -> None:
    assert (original_root / "value.txt").read_text(encoding="utf-8") == "original\n"
    assert (replacement_root / "value.txt").read_text(encoding="utf-8") == "replacement\n"


def test_stable_alias_matches_canonical_snapshot(tmp_path: Path) -> None:
    requested, _, original_root, _, _ = _aliased_root(tmp_path)
    alias_archive = tmp_path / "alias.e2hsnap"
    canonical_archive = tmp_path / "canonical.e2hsnap"

    alias_manifest = create_snapshot(requested, alias_archive)
    canonical_manifest = create_snapshot(original_root, canonical_archive)

    assert alias_manifest == canonical_manifest
    assert alias_archive.read_bytes() == canonical_archive.read_bytes()


@pytest.mark.skipif(
    not snapshot_source._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative snapshot source traversal is unavailable",
)
def test_descriptor_snapshot_root_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested, alias, original_root, replacement_parent, replacement_root = _aliased_root(tmp_path)
    output = tmp_path / "descriptor.e2hsnap"
    state = _retarget_on_read(alias, replacement_parent, monkeypatch)

    with pytest.raises(SnapshotError, match="snapshot root changed during traversal"):
        create_snapshot(requested, output)

    assert state["swapped"] is True
    assert requested.resolve() == replacement_root
    assert not output.exists()
    _assert_trees_unchanged(original_root, replacement_root)


def test_fallback_snapshot_root_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested, alias, original_root, replacement_parent, replacement_root = _aliased_root(tmp_path)
    output = tmp_path / "fallback.e2hsnap"
    monkeypatch.setattr(snapshot_source, "_SOURCE_DIR_FD_SUPPORTED", False)
    state = _retarget_on_read(alias, replacement_parent, monkeypatch)

    with pytest.raises(SnapshotError, match="snapshot root changed during traversal"):
        create_snapshot(requested, output)

    assert state["swapped"] is True
    assert requested.resolve() == replacement_root
    assert not output.exists()
    _assert_trees_unchanged(original_root, replacement_root)


def test_snapshot_root_retarget_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested, alias, original_root, replacement_parent, replacement_root = _aliased_root(tmp_path)
    output = tmp_path / "publication.e2hsnap"
    original_collect = snapshot_source.collect_snapshot_source
    state = {"swapped": False}

    def swapping_collect(*args: Any, **kwargs: Any) -> tuple[list[Any], dict[str, bytes], int]:
        result = original_collect(*args, **kwargs)
        _retarget(alias, replacement_parent)
        state["swapped"] = True
        return result

    monkeypatch.setattr(snapshot_source, "collect_snapshot_source", swapping_collect)

    with pytest.raises(SnapshotError, match="snapshot root changed during traversal"):
        create_snapshot(requested, output)

    assert state["swapped"] is True
    assert requested.resolve() == replacement_root
    assert not output.exists()
    _assert_trees_unchanged(original_root, replacement_root)
