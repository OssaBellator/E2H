from __future__ import annotations

import os
from pathlib import PurePosixPath

import pytest

import e2h.workspace_archive as workspace_archive
from e2h.workspace_archive import WorkspaceArchiveError


def test_archive_name_rejects_excessive_directory_depth() -> None:
    relative = PurePosixPath(*(["d"] * (workspace_archive._MAX_ARCHIVE_DEPTH + 1)))

    with pytest.raises(WorkspaceArchiveError, match="maximum archive depth"):
        workspace_archive._archive_name(relative)


def test_archive_name_rejects_excessive_member_path_bytes() -> None:
    segment = "x" * 255
    segment_count = workspace_archive._MAX_ARCHIVE_MEMBER_PATH_BYTES // 255 + 2
    relative = PurePosixPath(*([segment] * segment_count))
    assert len(relative.parts) <= workspace_archive._MAX_ARCHIVE_DEPTH

    with pytest.raises(WorkspaceArchiveError, match="member path exceeds maximum archive bytes"):
        workspace_archive._archive_name(relative)


def test_support_probe_requires_memfd_cloexec(monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(workspace_archive.os, "MFD_CLOEXEC"):
        assert workspace_archive.sealed_workspace_archive_supported() is False
        return

    monkeypatch.delattr(workspace_archive.os, "MFD_CLOEXEC")
    assert workspace_archive.sealed_workspace_archive_supported() is False


@pytest.mark.skipif(
    not workspace_archive.sealed_workspace_archive_supported(),
    reason="memfd descriptor cleanup requires Linux memfd sealing",
)
def test_open_memfd_closes_descriptor_if_fdopen_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = os.memfd_create(
        "e2h-fd-cleanup-test",
        flags=os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
    )
    real_close = os.close
    closed: list[int] = []

    def fake_memfd_create(name: str, flags: int = 0) -> int:
        del name, flags
        return descriptor

    def failing_fdopen(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("injected fdopen failure")

    def tracking_close(value: int) -> None:
        closed.append(value)
        real_close(value)

    monkeypatch.setattr(workspace_archive.os, "memfd_create", fake_memfd_create)
    monkeypatch.setattr(workspace_archive.os, "fdopen", failing_fdopen)
    monkeypatch.setattr(workspace_archive.os, "close", tracking_close)

    with pytest.raises(WorkspaceArchiveError, match="injected fdopen failure"):
        workspace_archive._open_memfd()

    assert closed == [descriptor]
    with pytest.raises(OSError):
        os.fstat(descriptor)
