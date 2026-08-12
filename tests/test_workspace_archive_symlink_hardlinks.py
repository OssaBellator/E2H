from __future__ import annotations

import os
from pathlib import Path

import pytest

from e2h.workspace_archive import (
    WorkspaceArchiveError,
    sealed_workspace_archive_supported,
    stable_workspace_archive,
)

pytestmark = pytest.mark.skipif(
    not sealed_workspace_archive_supported(),
    reason="sealed workspace archive capture is unavailable",
)


def test_sealed_capture_rejects_hard_linked_symlink_inode(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target"
    target.mkdir()
    first = workspace / "first-link"
    first.symlink_to("target")
    second = workspace / "second-link"
    try:
        os.link(first, second, follow_symlinks=False)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hard-linking symlink inodes is unavailable: {exc}")
    if first.lstat().st_ino != second.lstat().st_ino:
        pytest.skip("test filesystem did not hard-link the symlink inode")
    assert first.lstat().st_nlink > 1

    with pytest.raises(WorkspaceArchiveError, match="symlink .* has multiple hard links"):
        with stable_workspace_archive(
            workspace,
            max_bytes=1024,
            max_entries=10,
        ):
            pytest.fail("hard-linked symlink must not produce a replay archive")
