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
    reason="sealed workspace archives require Linux memfd seals",
)


def test_workspace_archive_rejects_multiply_linked_regular_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.txt"
    source.write_text("shared inode", encoding="utf-8")
    alias = workspace / "alias.txt"
    try:
        os.link(source, alias)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    assert source.stat().st_ino == alias.stat().st_ino
    assert source.stat().st_nlink >= 2

    with pytest.raises(WorkspaceArchiveError, match="multiple hard links"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("hard-linked workspace should fail closed")


def test_workspace_archive_rejects_multiply_linked_symlink_inode(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source-link"
    source.symlink_to("target")
    alias = workspace / "alias-link"
    try:
        os.link(source, alias, follow_symlinks=False)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard-linked symlinks unavailable: {exc}")

    source_info = source.lstat()
    alias_info = alias.lstat()
    assert source.is_symlink()
    assert alias.is_symlink()
    assert source_info.st_ino == alias_info.st_ino
    assert source_info.st_nlink >= 2

    with pytest.raises(WorkspaceArchiveError, match="symlink .*multiple hard links"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("hard-linked symlink workspace should fail closed")
