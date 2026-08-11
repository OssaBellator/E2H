from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import e2h.workspace_snapshot as workspace_snapshot
from e2h.workspace_snapshot import WorkspaceSnapshotError, stable_workspace_snapshot


def test_workspace_snapshot_rejects_root_metadata_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("inside", encoding="utf-8")
    workspace.chmod(0o755)
    original_copy_directory = workspace_snapshot._copy_directory
    changed = False

    def mutating_copy(*args: Any, **kwargs: Any) -> None:
        nonlocal changed
        original_copy_directory(*args, **kwargs)
        if not changed:
            changed = True
            workspace.chmod(0o700)

    monkeypatch.setattr(workspace_snapshot, "_copy_directory", mutating_copy)

    with pytest.raises(WorkspaceSnapshotError, match="root changed while copying"):
        with stable_workspace_snapshot(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("changed root metadata should not be yielded")

    assert changed is True
