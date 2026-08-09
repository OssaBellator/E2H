from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot_source as snapshot_source
from e2h.snapshot import SnapshotError, SnapshotLimits


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

    monkeypatch.setattr(snapshot_source.os, "fdopen", rewriting_fdopen)
    return before, state


@pytest.mark.skipif(
    not snapshot_source._SOURCE_DIR_FD_SUPPORTED,
    reason="descriptor-relative snapshot source traversal is unavailable",
)
def test_descriptor_snapshot_source_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "inside.txt"
    original_text = "inside-a\n"
    replacement_text = "inside-b\n"
    source.write_text(original_text, encoding="utf-8")
    before, state = _rewrite_on_read(source, replacement_text, monkeypatch)
    root_info = root.stat(follow_symlinks=False)

    with pytest.raises(SnapshotError, match="file changed while snapshotting"):
        snapshot_source.collect_snapshot_source(
            root,
            root_info,
            includes=(".",),
            excludes=(),
            ignored_paths=set(),
            limits=SnapshotLimits(max_entries=10, max_file_bytes=100, max_total_bytes=100),
        )

    assert state["rewritten"] is True
    after = source.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns
