"""Descriptor-bound private workspace snapshots for isolated remote replay."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Iterator

from e2h.directory_binding import DirectoryBindingError, open_absolute_directory

_READ_CHUNK_BYTES = 1024 * 1024


class WorkspaceSnapshotError(RuntimeError):
    """Raised when an isolated replay workspace cannot be copied safely."""


@dataclass
class _SnapshotState:
    max_bytes: int
    max_entries: int
    bytes_copied: int = 0
    entries_copied: int = 0

    def add_entry(self) -> None:
        self.entries_copied += 1
        if self.entries_copied > self.max_entries:
            raise WorkspaceSnapshotError(
                f"replay workspace exceeds configured max entries ({self.max_entries})"
            )

    def add_bytes(self, count: int) -> None:
        self.bytes_copied += count
        if self.bytes_copied > self.max_bytes:
            raise WorkspaceSnapshotError(
                f"replay workspace exceeds configured max bytes ({self.max_bytes})"
            )


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _directory_snapshot_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mtime_ns,
        info.st_mode,
    )


def _entry_identity(info: os.stat_result) -> tuple[int, ...]:
    if stat.S_ISDIR(info.st_mode):
        return _directory_snapshot_identity(info)
    return _file_identity(info)


def _apply_metadata(destination: Path, info: os.stat_result) -> None:
    destination.chmod(stat.S_IMODE(info.st_mode) & 0o777)
    os.utime(
        destination,
        ns=(info.st_atime_ns, info.st_mtime_ns),
        follow_symlinks=False,
    )


def _stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceSnapshotError(
            f"unable to inspect replay workspace entry {name!r}: {exc}"
        ) from exc


def _list_directory(descriptor: int) -> list[str]:
    try:
        return sorted(os.listdir(descriptor))
    except (OSError, TypeError) as exc:
        raise WorkspaceSnapshotError(
            f"unable to list replay workspace directory: {exc}"
        ) from exc


def _copy_regular_file(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
    destination: Path,
    state: _SnapshotState,
) -> None:
    if expected.st_size > state.max_bytes - state.bytes_copied:
        state.add_bytes(expected.st_size)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise WorkspaceSnapshotError(
            f"unable to open replay workspace file {name!r}: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _file_identity(opened) != _file_identity(expected)
        ):
            raise WorkspaceSnapshotError(
                f"replay workspace file {name!r} changed while opening"
            )
        copied = 0
        try:
            with (
                os.fdopen(descriptor, "rb", closefd=False) as source,
                destination.open("xb") as target,
            ):
                while chunk := source.read(_READ_CHUNK_BYTES):
                    copied += len(chunk)
                    if copied > expected.st_size:
                        raise WorkspaceSnapshotError(
                            f"replay workspace file {name!r} changed while copying"
                        )
                    target.write(chunk)
        except OSError as exc:
            raise WorkspaceSnapshotError(
                f"unable to copy replay workspace file {name!r}: {exc}"
            ) from exc
        if copied != expected.st_size:
            raise WorkspaceSnapshotError(
                f"replay workspace file {name!r} changed while copying"
            )
        state.add_bytes(copied)
        after = os.fstat(descriptor)
        current = _stat_entry(parent_descriptor, name)
        if (
            _file_identity(after) != _file_identity(expected)
            or _file_identity(current) != _file_identity(expected)
        ):
            raise WorkspaceSnapshotError(
                f"replay workspace file {name!r} changed while copying"
            )
        _apply_metadata(destination, expected)
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _safe_symlink_target(parent: PurePosixPath, target: str) -> None:
    if not target or "\x00" in target:
        raise WorkspaceSnapshotError(
            "replay workspace symlink target must be non-empty without NUL"
        )
    value = PurePosixPath(target)
    if value.is_absolute():
        raise WorkspaceSnapshotError("replay workspace symlink target must be relative")
    stack = [part for part in parent.parts if part not in {"", "."}]
    for part in value.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                raise WorkspaceSnapshotError(
                    "replay workspace symlink escapes isolated root"
                )
            stack.pop()
        else:
            stack.append(part)


def _copy_symlink(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
    destination: Path,
    relative_parent: PurePosixPath,
    state: _SnapshotState,
) -> None:
    try:
        target = os.readlink(name, dir_fd=parent_descriptor)
    except OSError as exc:
        raise WorkspaceSnapshotError(
            f"unable to read replay workspace symlink {name!r}: {exc}"
        ) from exc
    _safe_symlink_target(relative_parent, target)
    state.add_bytes(len(target.encode("utf-8", errors="surrogateescape")))
    try:
        destination.symlink_to(target)
    except OSError as exc:
        raise WorkspaceSnapshotError(
            f"unable to copy replay workspace symlink {name!r}: {exc}"
        ) from exc
    current = _stat_entry(parent_descriptor, name)
    try:
        current_target = os.readlink(name, dir_fd=parent_descriptor)
    except OSError as exc:
        raise WorkspaceSnapshotError(
            f"unable to re-read replay workspace symlink {name!r}: {exc}"
        ) from exc
    if _file_identity(current) != _file_identity(expected) or current_target != target:
        raise WorkspaceSnapshotError(
            f"replay workspace symlink {name!r} changed while copying"
        )


def _copy_directory_entry(
    source_descriptor: int,
    name: str,
    info: os.stat_result,
    destination: Path,
    relative: PurePosixPath,
    state: _SnapshotState,
) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        child_descriptor = os.open(name, flags, dir_fd=source_descriptor)
    except OSError as exc:
        raise WorkspaceSnapshotError(
            f"unable to open replay workspace directory {name!r}: {exc}"
        ) from exc
    try:
        opened = os.fstat(child_descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _directory_snapshot_identity(opened)
            != _directory_snapshot_identity(info)
        ):
            raise WorkspaceSnapshotError(
                f"replay workspace directory {name!r} changed while opening"
            )
        destination.mkdir(mode=0o700)
        _copy_directory(child_descriptor, destination, relative / name, state)
        current = _stat_entry(source_descriptor, name)
        if (
            _directory_snapshot_identity(os.fstat(child_descriptor))
            != _directory_snapshot_identity(info)
            or _directory_snapshot_identity(current)
            != _directory_snapshot_identity(info)
        ):
            raise WorkspaceSnapshotError(
                f"replay workspace directory {name!r} changed while copying"
            )
        _apply_metadata(destination, info)
    finally:
        with suppress(OSError):
            os.close(child_descriptor)


def _copy_directory(
    source_descriptor: int,
    destination: Path,
    relative: PurePosixPath,
    state: _SnapshotState,
) -> None:
    names = _list_directory(source_descriptor)
    expected: dict[str, os.stat_result] = {}
    for name in names:
        state.add_entry()
        info = _stat_entry(source_descriptor, name)
        expected[name] = info
        target = destination / name
        if stat.S_ISREG(info.st_mode):
            _copy_regular_file(source_descriptor, name, info, target, state)
            continue
        if stat.S_ISLNK(info.st_mode):
            _copy_symlink(source_descriptor, name, info, target, relative, state)
            continue
        if stat.S_ISDIR(info.st_mode):
            _copy_directory_entry(
                source_descriptor,
                name,
                info,
                target,
                relative,
                state,
            )
            continue
        raise WorkspaceSnapshotError(
            f"replay workspace entry {name!r} has unsupported file type"
        )

    if _list_directory(source_descriptor) != names:
        raise WorkspaceSnapshotError("replay workspace directory changed while copying")
    for name, before in expected.items():
        current = _stat_entry(source_descriptor, name)
        if _entry_identity(current) != _entry_identity(before):
            raise WorkspaceSnapshotError(
                f"replay workspace entry {name!r} changed while copying"
            )


@contextmanager
def stable_workspace_snapshot(
    workspace: Path,
    *,
    max_bytes: int,
    max_entries: int,
) -> Iterator[Path]:
    """Yield a private bounded copy of one descriptor-bound replay workspace."""
    if max_bytes < 1:
        raise WorkspaceSnapshotError("max replay workspace bytes must be positive")
    if max_entries < 1:
        raise WorkspaceSnapshotError("max replay workspace entries must be positive")
    try:
        descriptor = open_absolute_directory(workspace)
    except DirectoryBindingError as exc:
        raise WorkspaceSnapshotError(str(exc)) from exc
    try:
        root_info = os.fstat(descriptor)
        with TemporaryDirectory(prefix="e2h-replay-workspace-") as temporary:
            private_workspace = Path(temporary) / "workspace"
            private_workspace.mkdir(mode=0o700)
            state = _SnapshotState(max_bytes=max_bytes, max_entries=max_entries)
            _copy_directory(
                descriptor,
                private_workspace,
                PurePosixPath("."),
                state,
            )
            if _directory_snapshot_identity(os.fstat(descriptor)) != (
                _directory_snapshot_identity(root_info)
            ):
                raise WorkspaceSnapshotError(
                    "replay workspace root changed while copying"
                )
            _apply_metadata(private_workspace, root_info)
            yield private_workspace
    except OSError as exc:
        raise WorkspaceSnapshotError(
            f"unable to create isolated replay workspace: {exc}"
        ) from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)
