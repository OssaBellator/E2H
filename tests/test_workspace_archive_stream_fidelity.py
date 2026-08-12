from __future__ import annotations

import os
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO

import pytest

import e2h.workspace_archive as workspace_archive
from e2h.workspace_archive import (
    WorkspaceArchiveError,
    sealed_workspace_archive_supported,
    stable_workspace_archive,
)

pytestmark = pytest.mark.skipif(
    not sealed_workspace_archive_supported(),
    reason="sealed workspace archives require Linux memfd seals",
)


class _ExtraByteReader:
    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self._injected = False

    def read(self, size: int = -1) -> bytes:
        value = self._handle.read(size)
        if value:
            return value
        if not self._injected:
            self._injected = True
            return b"x"
        return b""

    def __enter__(self) -> _ExtraByteReader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self._handle.close()


def test_workspace_archive_rejects_regular_stream_beyond_reported_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.bin").write_bytes(b"abc")
    original_fdopen = workspace_archive.os.fdopen

    def injected_fdopen(
        descriptor: int,
        mode: str = "r",
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        handle = original_fdopen(descriptor, mode, *args, **kwargs)
        if mode == "rb" and kwargs.get("closefd") is False:
            return _ExtraByteReader(handle)
        return handle

    monkeypatch.setattr(workspace_archive.os, "fdopen", injected_fdopen)

    with pytest.raises(WorkspaceArchiveError, match="data beyond reported size"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("stream with bytes beyond st_size must not be archived")


def test_workspace_archive_rejects_symlink_target_size_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "link"
    try:
        link.symlink_to("a")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    original_readlink = workspace_archive.os.readlink

    def mismatched_readlink(path: Any, *args: Any, **kwargs: Any) -> str:
        if path == link.name and kwargs.get("dir_fd") is not None:
            return "bb"
        return original_readlink(path, *args, **kwargs)

    monkeypatch.setattr(workspace_archive.os, "readlink", mismatched_readlink)

    with pytest.raises(WorkspaceArchiveError, match="symlink target size changed"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("symlink target size mismatch must not be archived")
