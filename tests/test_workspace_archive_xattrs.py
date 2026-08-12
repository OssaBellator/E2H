from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

import e2h.workspace_archive as workspace_archive
from e2h.workspace_archive import (
    WorkspaceArchiveError,
    sealed_workspace_archive_supported,
    stable_workspace_archive,
)

pytestmark = pytest.mark.skipif(
    not sealed_workspace_archive_supported(),
    reason="sealed workspace archive capture is unavailable",
)

_XATTR_UNAVAILABLE = {errno.ENOTSUP, errno.EPERM, errno.EACCES}
if hasattr(errno, "EOPNOTSUPP"):
    _XATTR_UNAVAILABLE.add(errno.EOPNOTSUPP)


def _set_test_xattr(path: Path) -> None:
    if not hasattr(os, "setxattr"):
        pytest.skip("extended attributes are unavailable")
    try:
        os.setxattr(path, "user.e2h-test", b"value")
    except OSError as exc:
        if exc.errno in _XATTR_UNAVAILABLE:
            pytest.skip(f"test filesystem cannot set user xattrs: {exc}")
        raise


def test_sealed_capture_rejects_regular_file_xattrs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "marker.txt"
    marker.write_text("trusted", encoding="utf-8")
    _set_test_xattr(marker)

    with pytest.raises(WorkspaceArchiveError, match="extended attributes that cannot be preserved"):
        with stable_workspace_archive(
            workspace,
            max_bytes=1024,
            max_entries=10,
        ):
            pytest.fail("xattr-bearing file must not produce a replay archive")


def test_sealed_capture_rejects_workspace_root_xattrs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _set_test_xattr(workspace)

    with pytest.raises(WorkspaceArchiveError, match="root has extended attributes"):
        with stable_workspace_archive(
            workspace,
            max_bytes=1024,
            max_entries=10,
        ):
            pytest.fail("xattr-bearing root must not produce a replay archive")


def test_sealed_capture_fails_closed_when_xattrs_cannot_be_inspected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def denied(*args: object, **kwargs: object) -> list[str]:
        del args, kwargs
        raise PermissionError(errno.EACCES, "xattr inspection denied")

    monkeypatch.setattr(workspace_archive.os, "listxattr", denied)

    with pytest.raises(WorkspaceArchiveError, match="unable to inspect.*extended attributes"):
        with stable_workspace_archive(
            workspace,
            max_bytes=1024,
            max_entries=10,
        ):
            pytest.fail("uninspectable xattrs must fail closed")
