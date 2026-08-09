from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot as snapshot
from e2h.snapshot import SnapshotError, SnapshotLimits


def _rewrite_on_fdopen(
    source: Path,
    replacement: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[os.stat_result, dict[str, bool]]:
    before = source.stat(follow_symlinks=False)
    original_fdopen = os.fdopen
    state = {"rewritten": False}

    def rewriting_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["rewritten"]:
            state["rewritten"] = True
            source.write_bytes(replacement)
            os.utime(
                source,
                ns=(before.st_atime_ns, before.st_mtime_ns),
                follow_symlinks=False,
            )
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(snapshot.os, "fdopen", rewriting_fdopen)
    return before, state


def _assert_rewrite_shape(before: os.stat_result, source: Path) -> None:
    after = source.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns


def test_fallback_snapshot_source_read_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "inside.txt"
    original = b"inside-a\n"
    replacement = b"inside-b\n"
    source.write_bytes(original)
    expected = source.stat(follow_symlinks=False)
    before, state = _rewrite_on_fdopen(source, replacement, monkeypatch)

    with pytest.raises(SnapshotError, match="file changed while snapshotting"):
        snapshot._read_file(
            root,
            source,
            "inside.txt",
            SnapshotLimits(max_entries=10, max_file_bytes=100, max_total_bytes=100),
            expected,
        )

    assert state["rewritten"] is True
    _assert_rewrite_shape(before, source)


@pytest.mark.skipif(
    not snapshot._OPEN_SUPPORTS_DIR_FD or not snapshot._STAT_SUPPORTS_DIR_FD,
    reason="descriptor-relative snapshot archive reads are unavailable",
)
def test_descriptor_archive_read_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "snapshot.zip"
    original = b"archive-a\n"
    replacement = b"archive-b\n"
    archive.write_bytes(original)
    before, state = _rewrite_on_fdopen(archive, replacement, monkeypatch)

    with pytest.raises(SnapshotError, match="snapshot archive changed while reading"):
        with snapshot._open_snapshot_archive_file(archive) as handle:
            handle.read(1)

    assert state["rewritten"] is True
    _assert_rewrite_shape(before, archive)


def test_fallback_archive_read_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "snapshot.zip"
    original = b"archive-a\n"
    replacement = b"archive-b\n"
    archive.write_bytes(original)
    monkeypatch.setattr(snapshot, "_OPEN_SUPPORTS_DIR_FD", False)
    monkeypatch.setattr(snapshot, "_STAT_SUPPORTS_DIR_FD", False)
    before, state = _rewrite_on_fdopen(archive, replacement, monkeypatch)

    with pytest.raises(SnapshotError, match="snapshot archive changed while reading"):
        with snapshot._open_snapshot_archive_file(archive) as handle:
            handle.read(1)

    assert state["rewritten"] is True
    _assert_rewrite_shape(before, archive)
