from __future__ import annotations

import errno
import os
import subprocess
import sys
import tarfile
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
    reason="sealed workspace archives require Linux memfd seals",
)


def test_workspace_archive_is_sealed_and_preserves_safe_tree(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "task"
    nested.mkdir(parents=True)
    workspace.chmod(0o751)
    shared = workspace / "shared.txt"
    shared.write_text("shared", encoding="utf-8")
    script = nested / "run.sh"
    script.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    script.chmod(0o755)
    link = nested / "shared-link"
    try:
        link.symlink_to("../shared.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with stable_workspace_archive(
        workspace.resolve(),
        max_bytes=1024,
        max_entries=10,
    ) as capture:
        assert capture.directories == frozenset({".", "task"})
        assert capture.entries == 4
        assert capture.source_bytes == (
            len(b"shared") + len(b"#!/bin/sh\necho ok\n") + len(b"../shared.txt")
        )
        assert capture.archive_bytes > capture.source_bytes

        with pytest.raises(OSError) as sealed:
            capture.file.write(b"replacement")
        assert sealed.value.errno == errno.EPERM

        capture.file.seek(0)
        with tarfile.open(fileobj=capture.file, mode="r:*") as archive:
            assert archive.getnames() == [
                ".",
                "shared.txt",
                "task",
                "task/run.sh",
                "task/shared-link",
            ]
            root = archive.getmember(".")
            assert root.isdir()
            assert root.mode == 0o751
            shared_member = archive.extractfile("shared.txt")
            assert shared_member is not None
            assert shared_member.read() == b"shared"
            script_member = archive.getmember("task/run.sh")
            assert script_member.mode == 0o755
            script_file = archive.extractfile(script_member)
            assert script_file is not None
            assert script_file.read().startswith(b"#!/bin/sh")
            link_member = archive.getmember("task/shared-link")
            assert link_member.issym()
            assert link_member.linkname == "../shared.txt"


def test_same_uid_process_cannot_rewrite_sealed_archive(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("trusted", encoding="utf-8")

    with stable_workspace_archive(
        workspace.resolve(),
        max_bytes=1024,
        max_entries=10,
    ) as capture:
        fd_path = f"/proc/{os.getpid()}/fd/{capture.file.fileno()}"
        script = (
            "import os,sys\n"
            "try:\n"
            " fd=os.open(sys.argv[1],os.O_WRONLY);os.write(fd,b'replaced');os.close(fd)\n"
            "except OSError as exc:\n"
            " print(exc.errno);raise SystemExit(0)\n"
            "raise SystemExit(1)\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script, fd_path],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0
        assert completed.stdout.strip() in {str(errno.EPERM), str(errno.EACCES)}

        capture.file.seek(0)
        with tarfile.open(fileobj=capture.file, mode="r:*") as archive:
            marker = archive.extractfile("marker.txt")
            assert marker is not None
            assert marker.read() == b"trusted"


def test_workspace_archive_enforces_byte_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "data.bin").write_bytes(b"12345")

    with pytest.raises(WorkspaceArchiveError, match=r"max bytes \(4\)"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=4,
            max_entries=10,
        ):
            raise AssertionError("oversized workspace should not be archived")


def test_workspace_archive_enforces_entry_limit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a").write_text("a", encoding="utf-8")
    (workspace / "b").write_text("b", encoding="utf-8")

    with pytest.raises(WorkspaceArchiveError, match=r"max entries \(1\)"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=1,
        ):
            raise AssertionError("workspace with too many entries should not be archived")


def test_workspace_archive_rejects_absolute_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "escape"
    try:
        link.symlink_to("/etc/passwd")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(WorkspaceArchiveError, match="symlink target must be relative"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("absolute symlink should not be archived")


def test_workspace_archive_rejects_relative_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "escape"
    try:
        link.symlink_to("../outside")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(WorkspaceArchiveError, match="symlink escapes isolated root"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("escaping symlink should not be archived")


def test_workspace_archive_rejects_special_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    fifo = workspace / "pipe"
    try:
        os.mkfifo(fifo)
    except (AttributeError, OSError) as exc:
        pytest.skip(f"FIFO creation unavailable: {exc}")

    with pytest.raises(WorkspaceArchiveError, match="unsupported file type"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("special file should not be archived")


def test_workspace_archive_rejects_file_replacement_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "data.txt"
    source.write_text("inside", encoding="utf-8")
    replacement = workspace / "replacement.txt"
    replacement.write_text("outside", encoding="utf-8")
    moved = workspace / "original.txt"
    original_open = workspace_archive.os.open
    swapped = False

    def swapping_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if not swapped and kwargs.get("dir_fd") is not None and target == source.name:
            swapped = True
            source.rename(moved)
            replacement.rename(source)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(workspace_archive.os, "open", swapping_open)

    with pytest.raises(WorkspaceArchiveError, match="changed while opening"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("replaced source file should not be archived")

    assert swapped is True


def test_workspace_archive_rejects_bound_root_path_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "marker.txt").write_text("outside", encoding="utf-8")
    moved = tmp_path / "original-workspace"
    original_add_directory = workspace_archive._add_directory
    swapped = False

    def rebinding_archive(*args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            workspace.rename(moved)
            try:
                workspace.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable: {exc}")
        original_add_directory(*args, **kwargs)

    monkeypatch.setattr(workspace_archive, "_add_directory", rebinding_archive)

    with pytest.raises(WorkspaceArchiveError, match="root changed while archiving"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("rebound workspace root should not be yielded")

    assert swapped is True
    assert (moved / "marker.txt").read_text(encoding="utf-8") == "inside"
    assert (workspace / "marker.txt").read_text(encoding="utf-8") == "outside"


def test_workspace_archive_rejects_root_metadata_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("inside", encoding="utf-8")
    workspace.chmod(0o755)
    original_add_directory = workspace_archive._add_directory
    changed = False

    def mutating_archive(*args: Any, **kwargs: Any) -> None:
        nonlocal changed
        original_add_directory(*args, **kwargs)
        if not changed:
            changed = True
            workspace.chmod(0o700)

    monkeypatch.setattr(workspace_archive, "_add_directory", mutating_archive)

    with pytest.raises(WorkspaceArchiveError, match="root changed while archiving"):
        with stable_workspace_archive(
            workspace.resolve(),
            max_bytes=1024,
            max_entries=10,
        ):
            raise AssertionError("changed root metadata should not be yielded")

    assert changed is True
