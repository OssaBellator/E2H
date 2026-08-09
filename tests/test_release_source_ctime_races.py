from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.release_source as release_source
from e2h.release_source import ReleaseSourceError, source_tree_sha256


def _rewrite_source_on_read(
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

    monkeypatch.setattr(release_source.os, "fdopen", rewriting_fdopen)
    return before, state


def _assert_identity_shape(before: os.stat_result, source: Path) -> None:
    after = source.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns


@pytest.mark.skipif(
    not release_source._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative release source traversal is unavailable",
)
def test_descriptor_source_hash_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "module.py"
    original_text = "value = 'inside-a'\n"
    replacement_text = "value = 'inside-b'\n"
    source.write_text(original_text, encoding="utf-8")
    before, state = _rewrite_source_on_read(source, replacement_text, monkeypatch)

    with pytest.raises(ReleaseSourceError, match="source file changed while hashing"):
        source_tree_sha256(root)

    assert state["rewritten"] is True
    _assert_identity_shape(before, source)


def test_fallback_source_hash_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "module.py"
    original_text = "value = 'inside-a'\n"
    replacement_text = "value = 'inside-b'\n"
    source.write_text(original_text, encoding="utf-8")
    monkeypatch.setattr(release_source, "_SOURCE_DIR_FD_SUPPORTED", False)
    before, state = _rewrite_source_on_read(source, replacement_text, monkeypatch)

    with pytest.raises(ReleaseSourceError, match="source file changed while hashing"):
        source_tree_sha256(root)

    assert state["rewritten"] is True
    _assert_identity_shape(before, source)
