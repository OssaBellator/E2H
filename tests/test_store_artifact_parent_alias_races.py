from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.store_rows as store_rows
from e2h.store_rows import ArtifactError, read_artifact


def _aliased_artifact(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    original = tmp_path / "original"
    original.mkdir()
    artifact = original / "artifact.json"
    artifact.write_text('{"value":1}\n', encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    alias = tmp_path / "visible-parent"
    alias.symlink_to(original, target_is_directory=True)
    return alias, original, replacement, alias / artifact.name


def _retarget_on_read(
    alias: Path,
    replacement: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bool]:
    original_fdopen = os.fdopen
    state = {"swapped": False}

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["swapped"]:
            state["swapped"] = True
            alias.unlink()
            alias.symlink_to(replacement, target_is_directory=True)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(store_rows.os, "fdopen", swapping_fdopen)
    return state


def _rewrite_on_read(
    source: Path,
    replacement_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[os.stat_result, dict[str, bool]]:
    before = source.stat(follow_symlinks=False)
    original_fdopen = os.fdopen
    state = {"rewritten": False}

    def rewriting_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["rewritten"]:
            state["rewritten"] = True
            source.write_text(replacement_text, encoding="utf-8")
            os.utime(
                source,
                ns=(before.st_atime_ns, before.st_mtime_ns),
                follow_symlinks=False,
            )
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(store_rows.os, "fdopen", rewriting_fdopen)
    return before, state


@pytest.mark.skipif(
    not store_rows._ARTIFACT_DIR_FD_SUPPORTED,
    reason="descriptor-relative artifact reads are unavailable",
)
def test_descriptor_artifact_read_rejects_parent_alias_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, artifact = _aliased_artifact(tmp_path)
    state = _retarget_on_read(alias, replacement, monkeypatch)

    with pytest.raises(ArtifactError, match="parent changed while being read"):
        read_artifact(artifact)

    assert state["swapped"] is True
    assert alias.resolve() == replacement
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert (original / artifact.name).read_text(encoding="utf-8") == '{"value":1}\n'


def test_fallback_artifact_read_rejects_parent_alias_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, artifact = _aliased_artifact(tmp_path)
    monkeypatch.setattr(store_rows, "_ARTIFACT_DIR_FD_SUPPORTED", False)
    state = _retarget_on_read(alias, replacement, monkeypatch)

    with pytest.raises(ArtifactError, match="parent changed while being read"):
        read_artifact(artifact)

    assert state["swapped"] is True
    assert alias.resolve() == replacement
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert (original / artifact.name).read_text(encoding="utf-8") == '{"value":1}\n'


@pytest.mark.skipif(
    not store_rows._ARTIFACT_DIR_FD_SUPPORTED,
    reason="descriptor-relative artifact reads are unavailable",
)
def test_descriptor_artifact_read_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.json"
    original_text = '{"value":1}\n'
    replacement_text = '{"value":2}\n'
    artifact.write_text(original_text, encoding="utf-8")
    before, state = _rewrite_on_read(artifact, replacement_text, monkeypatch)

    with pytest.raises(ArtifactError, match="artifact changed while being read"):
        read_artifact(artifact)

    assert state["rewritten"] is True
    after = artifact.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns


def test_fallback_artifact_read_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.json"
    original_text = '{"value":1}\n'
    replacement_text = '{"value":2}\n'
    artifact.write_text(original_text, encoding="utf-8")
    monkeypatch.setattr(store_rows, "_ARTIFACT_DIR_FD_SUPPORTED", False)
    before, state = _rewrite_on_read(artifact, replacement_text, monkeypatch)

    with pytest.raises(ArtifactError, match="artifact changed while being read"):
        read_artifact(artifact)

    assert state["rewritten"] is True
    after = artifact.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns
