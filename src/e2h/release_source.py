"""Deterministic identity for release source trees."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from e2h.snapshot import (
    DEFAULT_EXCLUDES,
    SnapshotCore,
    SnapshotEntry,
    SnapshotLimits,
    revalidate_snapshot_limits,
    snapshot_id,
)

_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_LISTDIR_SUPPORTS_FD = os.listdir in os.supports_fd
_SOURCE_DIR_FD_SUPPORTED = _OPEN_SUPPORTS_DIR_FD and _STAT_SUPPORTS_DIR_FD and _LISTDIR_SUPPORTS_FD


class ReleaseSourceError(ValueError):
    """Raised when a release source tree cannot be identified safely."""


@dataclass
class _SourceDirectoryFrame:
    descriptor: int
    opened: os.stat_result
    names: list[str]
    parts: tuple[str, ...]
    parent_descriptor: int | None = None
    name: str | None = None
    index: int = 0


def _matches_exclude(relative: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _resolve_source_root(root: Path) -> tuple[Path, os.stat_result]:
    try:
        root_info = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to inspect release source root: {exc}") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ReleaseSourceError("release source root must be a real directory")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_info = resolved_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to inspect release source root: {exc}") from exc
    if _stat_identity(resolved_info) != _stat_identity(root_info):
        raise ReleaseSourceError("release source root changed during traversal")
    return resolved_root, resolved_info


def _requested_source_root_must_be_stable(
    requested_root: Path,
    expected: os.stat_result,
) -> None:
    try:
        current_root = requested_root.resolve(strict=True)
        current = current_root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to restat release source root: {exc}") from exc
    if not stat.S_ISDIR(current.st_mode) or _stat_identity(current) != _stat_identity(expected):
        raise ReleaseSourceError("release source root changed during traversal")


def _source_root_path_must_be_stable(
    root: Path,
    expected: os.stat_result,
    requested_root: Path,
) -> None:
    try:
        current = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to restat release source root: {exc}") from exc
    if not stat.S_ISDIR(current.st_mode) or _stat_identity(current) != _stat_identity(expected):
        raise ReleaseSourceError("release source root changed during traversal")
    _requested_source_root_must_be_stable(requested_root, expected)


def _source_root_descriptor_must_be_stable(
    root: Path,
    descriptor: int,
    opened: os.stat_result,
    requested_root: Path,
) -> None:
    try:
        after = os.fstat(descriptor)
        current = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to restat release source root: {exc}") from exc
    if (
        not stat.S_ISDIR(after.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or _stat_identity(after) != _stat_identity(opened)
        or _stat_identity(current) != _stat_identity(opened)
    ):
        raise ReleaseSourceError("release source root changed during traversal")
    _requested_source_root_must_be_stable(requested_root, opened)


def _resolve_under_root(root: Path, path: Path, relative: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to resolve source entry {relative}: {exc}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ReleaseSourceError(f"source path escapes root: {relative}") from exc
    return resolved


def _list_source_directory_path(
    root: Path,
    path: Path,
    relative: str,
    expected: os.stat_result,
) -> list[Path]:
    _resolve_under_root(root, path, relative)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to open source directory {relative}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise ReleaseSourceError(f"source entry is no longer a directory: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseSourceError(f"source directory changed while opening: {relative}")
        try:
            names = (
                sorted(os.listdir(descriptor))
                if os.listdir in os.supports_fd
                else sorted(os.listdir(path))
            )
        except OSError as exc:
            raise ReleaseSourceError(f"unable to list source directory {relative}: {exc}") from exc
        after = os.fstat(descriptor)
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReleaseSourceError(
                f"unable to restat source directory {relative}: {exc}"
            ) from exc
        _resolve_under_root(root, path, relative)
        if _stat_identity(after) != _stat_identity(opened) or _stat_identity(
            current
        ) != _stat_identity(opened):
            raise ReleaseSourceError(f"source directory changed while listing: {relative}")
        return [path / name for name in names]
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _hash_regular_file_path(
    root: Path,
    path: Path,
    relative: str,
    expected: os.stat_result,
    *,
    limits: SnapshotLimits,
) -> tuple[str, int, bool]:
    _resolve_under_root(root, path, relative)
    if expected.st_size > limits.max_file_bytes:
        raise ReleaseSourceError(f"source file exceeds max_file_bytes: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to open source file {relative}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ReleaseSourceError(f"source entry is not a regular file: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseSourceError(f"source file changed while opening: {relative}")
        if opened.st_size > limits.max_file_bytes:
            raise ReleaseSourceError(f"source file exceeds max_file_bytes: {relative}")
        digest = hashlib.sha256()
        observed = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                observed += len(chunk)
                if observed > limits.max_file_bytes:
                    raise ReleaseSourceError(f"source file exceeds max_file_bytes: {relative}")
                digest.update(chunk)
        after = os.fstat(descriptor)
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReleaseSourceError(f"unable to restat source file {relative}: {exc}") from exc
        _resolve_under_root(root, path, relative)
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or observed != opened.st_size
        ):
            raise ReleaseSourceError(f"source file changed while hashing: {relative}")
        return digest.hexdigest(), observed, bool(opened.st_mode & stat.S_IXUSR)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to hash source file {relative}: {exc}") from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _source_tree_sha256_path(
    root: Path,
    root_info: os.stat_result,
    requested_root: Path,
    *,
    patterns: tuple[str, ...],
    limits: SnapshotLimits,
) -> str:
    children = _list_source_directory_path(root, root, ".", root_info)
    _source_root_path_must_be_stable(root, root_info, requested_root)

    stack = list(reversed(children))
    entries: list[SnapshotEntry] = []
    total_bytes = 0
    while stack:
        _source_root_path_must_be_stable(root, root_info, requested_root)
        current = stack.pop()
        try:
            relative = current.relative_to(root).as_posix()
        except ValueError as exc:
            raise ReleaseSourceError(f"source path escapes root: {current}") from exc
        if _matches_exclude(relative, patterns):
            continue
        if len(entries) >= limits.max_entries:
            raise ReleaseSourceError("release source tree exceeds max_entries")
        _resolve_under_root(root, current, relative)
        try:
            entry = current.stat(follow_symlinks=False)
        except OSError as exc:
            raise ReleaseSourceError(f"unable to stat source entry {relative}: {exc}") from exc
        if stat.S_ISLNK(entry.st_mode):
            raise ReleaseSourceError(
                f"symbolic links are not supported in release source: {relative}"
            )
        if stat.S_ISDIR(entry.st_mode):
            entries.append(SnapshotEntry(path=relative, kind="directory"))
            descendants = _list_source_directory_path(root, current, relative, entry)
            _source_root_path_must_be_stable(root, root_info, requested_root)
            stack.extend(reversed(descendants))
            continue
        if not stat.S_ISREG(entry.st_mode):
            raise ReleaseSourceError(f"unsupported release source entry: {relative}")
        digest, size_bytes, executable = _hash_regular_file_path(
            root,
            current,
            relative,
            entry,
            limits=limits,
        )
        _source_root_path_must_be_stable(root, root_info, requested_root)
        total_bytes += size_bytes
        if total_bytes > limits.max_total_bytes:
            raise ReleaseSourceError("release source tree exceeds max_total_bytes")
        entries.append(
            SnapshotEntry(
                path=relative,
                kind="file",
                sha256=digest,
                size_bytes=size_bytes,
                executable=executable,
            )
        )

    _source_root_path_must_be_stable(root, root_info, requested_root)
    core = SnapshotCore(
        entries=sorted(entries, key=lambda entry: entry.path),
        total_bytes=total_bytes,
    )
    return snapshot_id(core)


def _stat_source_entry(parent_descriptor: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)


def _open_source_root(root: Path, expected: os.stat_result) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to open source directory .: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise ReleaseSourceError("source entry is no longer a directory: .")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseSourceError("source directory changed while opening: .")
        return descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _open_bound_source_directory_at(
    parent_descriptor: int,
    name: str,
    relative: str,
    expected: os.stat_result,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to open source directory {relative}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise ReleaseSourceError(f"source entry is no longer a directory: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseSourceError(f"source directory changed while opening: {relative}")
        return descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _list_source_directory_at(
    descriptor: int,
    relative: str,
    opened: os.stat_result,
) -> list[str]:
    try:
        names = sorted(os.listdir(descriptor))
        after = os.fstat(descriptor)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to list source directory {relative}: {exc}") from exc
    if _stat_identity(after) != _stat_identity(opened):
        raise ReleaseSourceError(f"source directory changed while listing: {relative}")
    return names


def _source_directory_frame_must_be_stable(frame: _SourceDirectoryFrame) -> None:
    try:
        after = os.fstat(frame.descriptor)
        current = (
            _stat_source_entry(frame.parent_descriptor, frame.name)
            if frame.parent_descriptor is not None and frame.name is not None
            else None
        )
    except OSError as exc:
        relative = PurePosixPath(*frame.parts).as_posix() if frame.parts else "."
        raise ReleaseSourceError(f"unable to restat source directory {relative}: {exc}") from exc
    if _stat_identity(after) != _stat_identity(frame.opened):
        relative = PurePosixPath(*frame.parts).as_posix() if frame.parts else "."
        raise ReleaseSourceError(f"source directory changed while traversing: {relative}")
    if current is not None and _stat_identity(current) != _stat_identity(frame.opened):
        relative = PurePosixPath(*frame.parts).as_posix()
        raise ReleaseSourceError(f"source directory changed while traversing: {relative}")


def _hash_regular_file_at(
    parent_descriptor: int,
    name: str,
    relative: str,
    expected: os.stat_result,
    *,
    limits: SnapshotLimits,
) -> tuple[str, int, bool]:
    if expected.st_size > limits.max_file_bytes:
        raise ReleaseSourceError(f"source file exceeds max_file_bytes: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to open source file {relative}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ReleaseSourceError(f"source entry is not a regular file: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseSourceError(f"source file changed while opening: {relative}")
        if opened.st_size > limits.max_file_bytes:
            raise ReleaseSourceError(f"source file exceeds max_file_bytes: {relative}")
        digest = hashlib.sha256()
        observed = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                observed += len(chunk)
                if observed > limits.max_file_bytes:
                    raise ReleaseSourceError(f"source file exceeds max_file_bytes: {relative}")
                digest.update(chunk)
        after = os.fstat(descriptor)
        current = _stat_source_entry(parent_descriptor, name)
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or observed != opened.st_size
        ):
            raise ReleaseSourceError(f"source file changed while hashing: {relative}")
        return digest.hexdigest(), observed, bool(opened.st_mode & stat.S_IXUSR)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to hash source file {relative}: {exc}") from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _source_tree_sha256_descriptor(
    root: Path,
    root_info: os.stat_result,
    requested_root: Path,
    *,
    patterns: tuple[str, ...],
    limits: SnapshotLimits,
) -> str:
    root_descriptor, root_opened = _open_source_root(root, root_info)
    stack: list[_SourceDirectoryFrame] = []
    try:
        _source_root_descriptor_must_be_stable(
            root,
            root_descriptor,
            root_opened,
            requested_root,
        )
        root_names = _list_source_directory_at(root_descriptor, ".", root_opened)
        stack.append(
            _SourceDirectoryFrame(
                descriptor=root_descriptor,
                opened=root_opened,
                names=root_names,
                parts=(),
            )
        )
        entries: list[SnapshotEntry] = []
        total_bytes = 0
        while stack:
            frame = stack[-1]
            if frame.index >= len(frame.names):
                _source_directory_frame_must_be_stable(frame)
                if frame.parts:
                    with suppress(OSError):
                        os.close(frame.descriptor)
                stack.pop()
                continue

            name = frame.names[frame.index]
            frame.index += 1
            relative_parts = (*frame.parts, name)
            relative = PurePosixPath(*relative_parts).as_posix()
            if _matches_exclude(relative, patterns):
                continue
            if len(entries) >= limits.max_entries:
                raise ReleaseSourceError("release source tree exceeds max_entries")
            try:
                entry = _stat_source_entry(frame.descriptor, name)
            except OSError as exc:
                raise ReleaseSourceError(f"unable to stat source entry {relative}: {exc}") from exc
            if stat.S_ISLNK(entry.st_mode):
                raise ReleaseSourceError(
                    f"symbolic links are not supported in release source: {relative}"
                )
            if stat.S_ISDIR(entry.st_mode):
                entries.append(SnapshotEntry(path=relative, kind="directory"))
                child_descriptor, child_opened = _open_bound_source_directory_at(
                    frame.descriptor,
                    name,
                    relative,
                    entry,
                )
                try:
                    child_names = _list_source_directory_at(
                        child_descriptor,
                        relative,
                        child_opened,
                    )
                except Exception:
                    with suppress(OSError):
                        os.close(child_descriptor)
                    raise
                stack.append(
                    _SourceDirectoryFrame(
                        descriptor=child_descriptor,
                        opened=child_opened,
                        names=child_names,
                        parts=relative_parts,
                        parent_descriptor=frame.descriptor,
                        name=name,
                    )
                )
                continue
            if not stat.S_ISREG(entry.st_mode):
                raise ReleaseSourceError(f"unsupported release source entry: {relative}")
            digest, size_bytes, executable = _hash_regular_file_at(
                frame.descriptor,
                name,
                relative,
                entry,
                limits=limits,
            )
            total_bytes += size_bytes
            if total_bytes > limits.max_total_bytes:
                raise ReleaseSourceError("release source tree exceeds max_total_bytes")
            entries.append(
                SnapshotEntry(
                    path=relative,
                    kind="file",
                    sha256=digest,
                    size_bytes=size_bytes,
                    executable=executable,
                )
            )

        _source_root_descriptor_must_be_stable(
            root,
            root_descriptor,
            root_opened,
            requested_root,
        )
        core = SnapshotCore(
            entries=sorted(entries, key=lambda entry: entry.path),
            total_bytes=total_bytes,
        )
        return snapshot_id(core)
    finally:
        for frame in reversed(stack):
            if frame.descriptor != root_descriptor:
                with suppress(OSError):
                    os.close(frame.descriptor)
        with suppress(OSError):
            os.close(root_descriptor)


def source_tree_sha256(
    root: Path,
    *,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    limits: SnapshotLimits | None = None,
) -> str:
    """Return the canonical snapshot-core SHA-256 for one release source tree."""
    limits = revalidate_snapshot_limits(limits, error_type=ReleaseSourceError)
    requested_root = root.absolute()
    root, root_info = _resolve_source_root(root)
    patterns = tuple(excludes)
    if _SOURCE_DIR_FD_SUPPORTED:
        return _source_tree_sha256_descriptor(
            root,
            root_info,
            requested_root,
            patterns=patterns,
            limits=limits,
        )
    return _source_tree_sha256_path(
        root,
        root_info,
        requested_root,
        patterns=patterns,
        limits=limits,
    )
