from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.release_source as release_source
from e2h.release_source import ReleaseSourceError, source_tree_sha256


def _aliased_source_root(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
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
    requested_root = alias / "workspace"
    return requested_root, alias, original_root, replacement_parent, replacement_root


def _retarget_alias_on_read(
    alias: Path,
    replacement_parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bool]:
    original_fdopen = os.fdopen
    state = {"swapped": False}

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["swapped"]:
            state["swapped"] = True
            alias.unlink()
            alias.symlink_to(replacement_parent, target_is_directory=True)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(release_source.os, "fdopen", swapping_fdopen)
    return state


def test_stable_aliased_source_root_matches_canonical_digest(tmp_path: Path) -> None:
    requested_root, _, original_root, _, _ = _aliased_source_root(tmp_path)

    assert source_tree_sha256(requested_root) == source_tree_sha256(original_root)


@pytest.mark.skipif(
    not release_source._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative release source traversal is unavailable",
)
def test_descriptor_source_hash_rejects_caller_visible_root_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_root, alias, original_root, replacement_parent, replacement_root = (
        _aliased_source_root(tmp_path)
    )
    state = _retarget_alias_on_read(alias, replacement_parent, monkeypatch)

    with pytest.raises(ReleaseSourceError, match="source root changed during traversal"):
        source_tree_sha256(requested_root)

    assert state["swapped"] is True
    assert requested_root.resolve() == replacement_root
    assert (original_root / "value.txt").read_text(encoding="utf-8") == "original\n"
    assert (replacement_root / "value.txt").read_text(encoding="utf-8") == "replacement\n"


def test_fallback_source_hash_rejects_caller_visible_root_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_root, alias, original_root, replacement_parent, replacement_root = (
        _aliased_source_root(tmp_path)
    )
    monkeypatch.setattr(release_source, "_SOURCE_DIR_FD_SUPPORTED", False)
    state = _retarget_alias_on_read(alias, replacement_parent, monkeypatch)

    with pytest.raises(ReleaseSourceError, match="source root changed during traversal"):
        source_tree_sha256(requested_root)

    assert state["swapped"] is True
    assert requested_root.resolve() == replacement_root
    assert (original_root / "value.txt").read_text(encoding="utf-8") == "original\n"
    assert (replacement_root / "value.txt").read_text(encoding="utf-8") == "replacement\n"
