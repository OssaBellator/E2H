from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

import e2h.workspace_archive as workspace_archive
from e2h.workspace_archive import (
    WorkspaceArchiveError,
    sealed_workspace_archive_supported,
    stable_workspace_archive,
)

pytestmark = pytest.mark.skipif(
    not sealed_workspace_archive_supported(),
    reason="sealed workspace archives require Linux descriptor and memfd primitives",
)


class _FakeEntry:
    def __init__(self, name: str) -> None:
        self.name = name


class _BoundedScanner:
    def __init__(self) -> None:
        self.index = 0

    def __enter__(self) -> _BoundedScanner:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __iter__(self) -> _BoundedScanner:
        return self

    def __next__(self) -> _FakeEntry:
        self.index += 1
        if self.index == 1:
            return _FakeEntry("a")
        if self.index == 2:
            return _FakeEntry("b")
        raise AssertionError("directory scanner advanced beyond max_names + 1")


def test_workspace_archive_stops_directory_scan_at_entry_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as scoped:
        scoped.setattr(workspace_archive.os, "scandir", lambda _descriptor: _BoundedScanner())
        state = workspace_archive._ArchiveState(max_bytes=1024, max_entries=1)
        with pytest.raises(WorkspaceArchiveError, match=r"max entries \(1\)"):
            workspace_archive._capture_directory_names(123, state)
        assert state.entries_copied == 2


def test_workspace_archive_rejects_membership_change_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("trusted", encoding="utf-8")
    before = workspace.stat()
    original_add_directory = workspace_archive._add_directory
    mutated = False

    def mutating_archive(*args: Any, **kwargs: Any) -> None:
        nonlocal mutated
        original_add_directory(*args, **kwargs)
        if mutated:
            return
        mutated = True
        time.sleep(0.01)
        transient = workspace / "transient"
        transient.write_text("x", encoding="utf-8")
        transient.unlink()
        os.utime(
            workspace,
            ns=(before.st_atime_ns, before.st_mtime_ns),
        )

    monkeypatch.setattr(workspace_archive, "_add_directory", mutating_archive)

    with pytest.raises(WorkspaceArchiveError, match="root changed while archiving"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("changed workspace root should not be yielded")

    after = workspace.stat()
    assert mutated is True
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns
