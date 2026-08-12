"""Descriptor-bound sealed tar capture for future remote container replay."""

from __future__ import annotations

import os
import stat
import sys
import tarfile
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - imported only on non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

from e2h.directory_binding import (
    DirectoryBindingError,
    directory_binding_supported,
    open_absolute_directory,
)

_MAX_ARCHIVE_DEPTH = 128
_MAX_ARCHIVE_MEMBER_PATH_BYTES = 4096


class WorkspaceArchiveError(RuntimeError):
    """Raised when a replay workspace cannot be captured into a stable archive."""


@dataclass
class _ArchiveState:
    max_bytes: int
    max_entries: int
    bytes_copied: int = 0
    entries_copied: int = 0

    def add_entry(self) -> None:
        self.entries_copied += 1
        if self.entries_copied > self.max_entries:
            raise WorkspaceArchiveError(
                f"replay workspace exceeds configured max entries ({self.max_entries})"
            )

    def add_bytes(self, count: int) -> None:
        self.bytes_copied += count
        if self.bytes_copied > self.max_bytes:
            raise WorkspaceArchiveError(
                f"replay workspace exceeds configured max bytes ({self.max_bytes})"
            )


@dataclass(frozen=True)
class WorkspaceArchive:
    """One sealed anonymous tar stream plus captured workspace metadata."""

    file: BinaryIO
    directories: frozenset[str]
    source_bytes: int
    entries: int
    archive_bytes: int


def sealed_workspace_archive_supported() -> bool:
    """Return whether this host can create and seal anonymous workspace archives."""
    return (
        sys.platform.startswith("linux")
        and directory_binding_supported()
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.readlink in os.supports_dir_fd
        and os.scandir in os.supports_fd
        and hasattr(os, "memfd_create")
        and hasattr(os, "MFD_CLOEXEC")
        and hasattr(os, "MFD_ALLOW_SEALING")
        and fcntl is not None
        and all(
            hasattr(fcntl, name)
            for name in (
                "F_ADD_SEALS",
                "F_GET_SEALS",
                "F_SEAL_SEAL",
                "F_SEAL_SHRINK",
                "F_SEAL_GROW",
                "F_SEAL_WRITE",
            )
        )
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


def _directory_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _entry_identity(info: os.stat_result) -> tuple[int, ...]:
    if stat.S_ISDIR(info.st_mode):
        return _directory_identity(info)
    return _file_identity(info)


def _stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError as exc:
        raise WorkspaceArchiveError(
            f"unable to inspect replay workspace entry {name!r}: {exc}"
        ) from exc


def _capture_directory_names(
    descriptor: int,
    state: _ArchiveState,
) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                state.add_entry()
                names.append(entry.name)
    except WorkspaceArchiveError:
        raise
    except (OSError, TypeError) as exc:
        raise WorkspaceArchiveError(
            f"unable to list replay workspace directory: {exc}"
        ) from exc
    return sorted(names)


def _list_directory(
    descriptor: int,
    *,
    max_names: int,
    overflow_message: str,
) -> list[str]:
    names: list[str] = []
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                if len(names) >= max_names:
                    raise WorkspaceArchiveError(overflow_message)
                names.append(entry.name)
    except WorkspaceArchiveError:
        raise
    except (OSError, TypeError) as exc:
        raise WorkspaceArchiveError(
            f"unable to list replay workspace directory: {exc}"
        ) from exc
    return sorted(names)


def _safe_symlink_target(parent: PurePosixPath, target: str) -> None:
    if not target or "\x00" in target:
        raise WorkspaceArchiveError(
            "replay workspace symlink target must be non-empty without NUL"
        )
    value = PurePosixPath(target)
    if value.is_absolute():
        raise WorkspaceArchiveError("replay workspace symlink target must be relative")
    stack = [part for part in parent.parts if part not in {"", "."}]
    for part in value.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                raise WorkspaceArchiveError("replay workspace symlink escapes isolated root")
            stack.pop()
        else:
            stack.append(part)


def _archive_name(relative: PurePosixPath) -> str:
    if len(relative.parts) > _MAX_ARCHIVE_DEPTH:
        raise WorkspaceArchiveError(
            f"replay workspace exceeds maximum archive depth ({_MAX_ARCHIVE_DEPTH})"
        )
    value = "." if str(relative) == "." else relative.as_posix()
    encoded = value.encode("utf-8", errors="surrogateescape")
    if len(encoded) > _MAX_ARCHIVE_MEMBER_PATH_BYTES:
        raise WorkspaceArchiveError(
            "replay workspace member path exceeds maximum archive bytes "
            f"({_MAX_ARCHIVE_MEMBER_PATH_BYTES})"
        )
    return value


def _tar_info(
    relative: PurePosixPath,
    info: os.stat_result,
    *,
    entry_type: bytes,
) -> tarfile.TarInfo:
    member = tarfile.TarInfo(_archive_name(relative))
    member.mode = stat.S_IMODE(info.st_mode)
    member.uid = info.st_uid
    member.gid = info.st_gid
    member.uname = ""
    member.gname = ""
    member.mtime = info.st_mtime
    member.type = entry_type
    return member


def _add_regular_file(
    archive: tarfile.TarFile,
    parent_descriptor: int,
    name: str,
    relative: PurePosixPath,
    expected: os.stat_result,
    state: _ArchiveState,
) -> None:
    if expected.st_nlink > 1:
        raise WorkspaceArchiveError(
            f"replay workspace regular file {name!r} has multiple hard links"
        )
    state.add_bytes(expected.st_size)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise WorkspaceArchiveError(
            f"unable to open replay workspace file {name!r}: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _file_identity(opened) != _file_identity(expected)
        ):
            raise WorkspaceArchiveError(
                f"replay workspace file {name!r} changed while opening"
            )
        member = _tar_info(relative, expected, entry_type=tarfile.REGTYPE)
        member.size = expected.st_size
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            archive.addfile(member, source)
        after = os.fstat(descriptor)
        current = _stat_entry(parent_descriptor, name)
        if (
            _file_identity(after) != _file_identity(expected)
            or _file_identity(current) != _file_identity(expected)
        ):
            raise WorkspaceArchiveError(
                f"replay workspace file {name!r} changed while archiving"
            )
    except (OSError, tarfile.TarError) as exc:
        raise WorkspaceArchiveError(
            f"unable to archive replay workspace file {name!r}: {exc}"
        ) from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _add_symlink(
    archive: tarfile.TarFile,
    parent_descriptor: int,
    name: str,
    relative: PurePosixPath,
    expected: os.stat_result,
    state: _ArchiveState,
) -> None:
    if expected.st_nlink > 1:
        raise WorkspaceArchiveError(
            f"replay workspace symlink {name!r} has multiple hard links"
        )
    try:
        target = os.readlink(name, dir_fd=parent_descriptor)
    except OSError as exc:
        raise WorkspaceArchiveError(
            f"unable to read replay workspace symlink {name!r}: {exc}"
        ) from exc
    _safe_symlink_target(relative.parent, target)
    state.add_bytes(len(target.encode("utf-8", errors="surrogateescape")))
    member = _tar_info(relative, expected, entry_type=tarfile.SYMTYPE)
    member.linkname = target
    member.size = 0
    try:
        archive.addfile(member)
        current = _stat_entry(parent_descriptor, name)
        current_target = os.readlink(name, dir_fd=parent_descriptor)
    except (OSError, tarfile.TarError) as exc:
        raise WorkspaceArchiveError(
            f"unable to archive replay workspace symlink {name!r}: {exc}"
        ) from exc
    if _file_identity(current) != _file_identity(expected) or current_target != target:
        raise WorkspaceArchiveError(
            f"replay workspace symlink {name!r} changed while archiving"
        )


def _add_directory_entry(
    archive: tarfile.TarFile,
    source_descriptor: int,
    name: str,
    relative: PurePosixPath,
    info: os.stat_result,
    state: _ArchiveState,
    directories: set[str],
) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        child_descriptor = os.open(name, flags, dir_fd=source_descriptor)
    except OSError as exc:
        raise WorkspaceArchiveError(
            f"unable to open replay workspace directory {name!r}: {exc}"
        ) from exc
    try:
        opened = os.fstat(child_descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _directory_identity(opened) != _directory_identity(info)
        ):
            raise WorkspaceArchiveError(
                f"replay workspace directory {name!r} changed while opening"
            )
        member = _tar_info(relative, info, entry_type=tarfile.DIRTYPE)
        member.size = 0
        archive.addfile(member)
        directories.add(relative.as_posix())
        _add_directory(archive, child_descriptor, relative, state, directories)
        current = _stat_entry(source_descriptor, name)
        if (
            _directory_identity(os.fstat(child_descriptor)) != _directory_identity(info)
            or _directory_identity(current) != _directory_identity(info)
        ):
            raise WorkspaceArchiveError(
                f"replay workspace directory {name!r} changed while archiving"
            )
    except (OSError, tarfile.TarError) as exc:
        raise WorkspaceArchiveError(
            f"unable to archive replay workspace directory {name!r}: {exc}"
        ) from exc
    finally:
        with suppress(OSError):
            os.close(child_descriptor)


def _add_directory(
    archive: tarfile.TarFile,
    source_descriptor: int,
    relative: PurePosixPath,
    state: _ArchiveState,
    directories: set[str],
) -> None:
    names = _capture_directory_names(source_descriptor, state)
    expected: dict[str, os.stat_result] = {}
    for name in names:
        info = _stat_entry(source_descriptor, name)
        expected[name] = info
        child_relative = relative / name if str(relative) != "." else PurePosixPath(name)
        if stat.S_ISREG(info.st_mode):
            _add_regular_file(archive, source_descriptor, name, child_relative, info, state)
            continue
        if stat.S_ISLNK(info.st_mode):
            _add_symlink(archive, source_descriptor, name, child_relative, info, state)
            continue
        if stat.S_ISDIR(info.st_mode):
            _add_directory_entry(
                archive,
                source_descriptor,
                name,
                child_relative,
                info,
                state,
                directories,
            )
            continue
        raise WorkspaceArchiveError(
            f"replay workspace entry {name!r} has unsupported file type"
        )

    current_names = _list_directory(
        source_descriptor,
        max_names=len(names),
        overflow_message="replay workspace directory changed while archiving",
    )
    if current_names != names:
        raise WorkspaceArchiveError("replay workspace directory changed while archiving")
    for name, before in expected.items():
        current = _stat_entry(source_descriptor, name)
        if _entry_identity(current) != _entry_identity(before):
            raise WorkspaceArchiveError(
                f"replay workspace entry {name!r} changed while archiving"
            )


def _seal_archive(handle: BinaryIO) -> None:
    if not sealed_workspace_archive_supported() or fcntl is None:
        raise WorkspaceArchiveError("sealed replay workspace archives are unavailable")
    seals = (
        fcntl.F_SEAL_WRITE
        | fcntl.F_SEAL_GROW
        | fcntl.F_SEAL_SHRINK
        | fcntl.F_SEAL_SEAL
    )
    try:
        handle.flush()
        fcntl.fcntl(handle.fileno(), fcntl.F_ADD_SEALS, seals)
        observed = fcntl.fcntl(handle.fileno(), fcntl.F_GET_SEALS)
    except OSError as exc:
        raise WorkspaceArchiveError(f"unable to seal replay workspace archive: {exc}") from exc
    if observed & seals != seals:
        raise WorkspaceArchiveError("replay workspace archive seal verification failed")


def _open_memfd() -> BinaryIO:
    if not sealed_workspace_archive_supported():
        raise WorkspaceArchiveError("sealed replay workspace archives are unavailable")
    flags = os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
    try:
        descriptor = os.memfd_create("e2h-replay-workspace", flags=flags)
    except OSError as exc:
        raise WorkspaceArchiveError(f"unable to create replay workspace memfd: {exc}") from exc
    try:
        return os.fdopen(descriptor, "w+b", buffering=0)
    except (OSError, ValueError) as exc:
        with suppress(OSError):
            os.close(descriptor)
        raise WorkspaceArchiveError(f"unable to create replay workspace memfd: {exc}") from exc


@contextmanager
def stable_workspace_archive(
    workspace: Path,
    *,
    max_bytes: int,
    max_entries: int,
) -> Iterator[WorkspaceArchive]:
    """Yield a sealed tar stream captured from one descriptor-bound workspace."""
    if max_bytes < 1:
        raise WorkspaceArchiveError("max replay workspace bytes must be positive")
    if max_entries < 1:
        raise WorkspaceArchiveError("max replay workspace entries must be positive")
    try:
        descriptor = open_absolute_directory(workspace)
    except DirectoryBindingError as exc:
        raise WorkspaceArchiveError(str(exc)) from exc

    try:
        try:
            root_info = os.fstat(descriptor)
        except OSError as exc:
            raise WorkspaceArchiveError(
                f"unable to inspect bound replay workspace root: {exc}"
            ) from exc
        state = _ArchiveState(max_bytes=max_bytes, max_entries=max_entries)
        directories = {"."}
        with _open_memfd() as handle:
            try:
                with tarfile.open(
                    fileobj=handle,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                    encoding="utf-8",
                    errors="surrogateescape",
                ) as archive:
                    root_member = _tar_info(
                        PurePosixPath("."),
                        root_info,
                        entry_type=tarfile.DIRTYPE,
                    )
                    root_member.size = 0
                    archive.addfile(root_member)
                    _add_directory(
                        archive,
                        descriptor,
                        PurePosixPath("."),
                        state,
                        directories,
                    )
            except (OSError, tarfile.TarError, UnicodeError) as exc:
                raise WorkspaceArchiveError(
                    f"unable to create replay workspace archive: {exc}"
                ) from exc
            try:
                root_current = os.fstat(descriptor)
            except OSError as exc:
                raise WorkspaceArchiveError(
                    f"unable to revalidate bound replay workspace root: {exc}"
                ) from exc
            if _directory_identity(root_current) != _directory_identity(root_info):
                raise WorkspaceArchiveError("replay workspace root changed while archiving")
            archive_bytes = handle.tell()
            _seal_archive(handle)
            handle.seek(0)
            yield WorkspaceArchive(
                file=handle,
                directories=frozenset(directories),
                source_bytes=state.bytes_copied,
                entries=state.entries_copied,
                archive_bytes=archive_bytes,
            )
    finally:
        with suppress(OSError):
            os.close(descriptor)
