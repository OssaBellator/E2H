"""Race-resistant source collection for workspace snapshots."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from e2h.snapshot import SnapshotEntry, SnapshotError, SnapshotLimits

_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_LISTDIR_SUPPORTS_FD = os.listdir in os.supports_fd
_SOURCE_DIR_FD_SUPPORTED = _OPEN_SUPPORTS_DIR_FD and _STAT_SUPPORTS_DIR_FD and _LISTDIR_SUPPORTS_FD


@dataclass
class _SourceDirectoryFrame:
    descriptor: int
    opened: os.stat_result
    names: list[str]
    parts: tuple[str, ...]
    parent_descriptor: int | None = None
    name: str | None = None
    index: int = 0


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def resolve_snapshot_source_root(root: Path) -> tuple[Path, os.stat_result]:
    """Resolve one snapshot root and bind the directory identity seen at that boundary."""
    try:
        resolved = root.resolve(strict=True)
        expected = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"unable to inspect snapshot root: {exc}") from exc
    if not stat.S_ISDIR(expected.st_mode):
        raise SnapshotError(f"snapshot root is not a directory: {resolved}")
    return resolved, expected


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _root_path_must_be_stable(root: Path, expected: os.stat_result) -> None:
    try:
        current = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"unable to restat snapshot root: {exc}") from exc
    if (
        not stat.S_ISDIR(current.st_mode)
        or _directory_identity(current) != _directory_identity(expected)
    ):
        raise SnapshotError("snapshot root changed during traversal")


def _root_path_contents_must_be_stable(root: Path, expected: os.stat_result) -> None:
    try:
        current = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"unable to restat snapshot root: {exc}") from exc
    if not stat.S_ISDIR(current.st_mode) or _stat_identity(current) != _stat_identity(expected):
        raise SnapshotError("snapshot root changed during traversal")


def _root_descriptor_must_be_stable(
    root: Path,
    descriptor: int,
    opened: os.stat_result,
) -> None:
    try:
        after = os.fstat(descriptor)
        current = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"unable to restat snapshot root: {exc}") from exc
    if (
        not stat.S_ISDIR(after.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or _stat_identity(after) != _stat_identity(opened)
        or _stat_identity(current) != _stat_identity(opened)
    ):
        raise SnapshotError("snapshot root changed during traversal")


def _include_parts(value: str) -> tuple[str, ...]:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise SnapshotError(f"include path must be a safe relative path: {value}")
    return () if str(pure) == "." else tuple(pure.parts)


def _matches_exclude(relative: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def _ignored_relatives(root: Path, ignored_paths: set[Path]) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in ignored_paths
        if path.is_relative_to(root) and path != root
    }


def _stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)


def _open_root(root: Path, expected: os.stat_result) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise SnapshotError(f"unable to open snapshot root: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _stat_identity(opened) != _stat_identity(expected):
            raise SnapshotError("snapshot root changed while opening")
        return descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _open_directory_at(
    parent_descriptor: int,
    name: str,
    relative: str,
    expected: os.stat_result,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SnapshotError(f"unable to open directory {relative}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise SnapshotError(f"snapshot entry is no longer a directory: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise SnapshotError(f"directory changed while opening: {relative}")
        return descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _list_directory_at(
    descriptor: int,
    relative: str,
    opened: os.stat_result,
) -> list[str]:
    try:
        names = sorted(os.listdir(descriptor))
        after = os.fstat(descriptor)
    except OSError as exc:
        raise SnapshotError(f"unable to list {relative}: {exc}") from exc
    if _stat_identity(after) != _stat_identity(opened):
        raise SnapshotError(f"directory changed while listing: {relative}")
    return names


def _frame_must_be_stable(frame: _SourceDirectoryFrame) -> None:
    try:
        after = os.fstat(frame.descriptor)
        current = (
            _stat_entry(frame.parent_descriptor, frame.name)
            if frame.parent_descriptor is not None and frame.name is not None
            else None
        )
    except OSError as exc:
        relative = PurePosixPath(*frame.parts).as_posix() if frame.parts else "."
        raise SnapshotError(f"unable to restat directory {relative}: {exc}") from exc
    relative = PurePosixPath(*frame.parts).as_posix() if frame.parts else "."
    if _stat_identity(after) != _stat_identity(frame.opened):
        raise SnapshotError(f"directory changed while traversing: {relative}")
    if current is not None and _stat_identity(current) != _stat_identity(frame.opened):
        raise SnapshotError(f"directory changed while traversing: {relative}")


def _read_file_at(
    parent_descriptor: int,
    name: str,
    relative: str,
    expected: os.stat_result,
    *,
    limits: SnapshotLimits,
) -> tuple[bytes, bool]:
    if expected.st_size > limits.max_file_bytes:
        raise SnapshotError(f"file exceeds max_file_bytes: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SnapshotError(f"unable to open {relative}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SnapshotError(f"snapshot entry is no longer a regular file: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise SnapshotError(f"file changed while opening: {relative}")
        if opened.st_size > limits.max_file_bytes:
            raise SnapshotError(f"file exceeds max_file_bytes: {relative}")
        data = bytearray()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                data.extend(chunk)
                if len(data) > limits.max_file_bytes:
                    raise SnapshotError(f"file exceeds max_file_bytes: {relative}")
        after = os.fstat(descriptor)
        current = _stat_entry(parent_descriptor, name)
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or len(data) != opened.st_size
        ):
            raise SnapshotError(f"file changed while snapshotting: {relative}")
        return bytes(data), bool(opened.st_mode & stat.S_IXUSR)
    except OSError as exc:
        raise SnapshotError(f"unable to read {relative}: {exc}") from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _open_guard_directories(
    root_descriptor: int,
    parts: tuple[str, ...],
) -> list[_SourceDirectoryFrame]:
    guards: list[_SourceDirectoryFrame] = []
    parent = root_descriptor
    try:
        for index, name in enumerate(parts):
            relative_parts = parts[: index + 1]
            relative = PurePosixPath(*relative_parts).as_posix()
            try:
                expected = _stat_entry(parent, name)
            except OSError as exc:
                raise SnapshotError(f"include path does not exist: {relative}") from exc
            if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
                raise SnapshotError(f"include path parent must be a directory: {relative}")
            descriptor, opened = _open_directory_at(parent, name, relative, expected)
            guards.append(
                _SourceDirectoryFrame(
                    descriptor=descriptor,
                    opened=opened,
                    names=[],
                    parts=relative_parts,
                    parent_descriptor=parent,
                    name=name,
                )
            )
            parent = descriptor
        return guards
    except Exception:
        for guard in reversed(guards):
            with suppress(OSError):
                os.close(guard.descriptor)
        raise


def _guards_must_be_stable(guards: list[_SourceDirectoryFrame]) -> None:
    for guard in guards:
        _frame_must_be_stable(guard)


def _ensure_entry_capacity(
    selected: dict[str, SnapshotEntry],
    limits: SnapshotLimits,
) -> None:
    if len(selected) >= limits.max_entries:
        raise SnapshotError("snapshot exceeds max_entries")


def _add_file(
    selected: dict[str, SnapshotEntry],
    blobs: dict[str, bytes],
    relative: str,
    data: bytes,
    executable: bool,
    *,
    limits: SnapshotLimits,
    total_bytes: int,
) -> int:
    if relative in selected:
        return total_bytes
    _ensure_entry_capacity(selected, limits)
    total_bytes += len(data)
    if total_bytes > limits.max_total_bytes:
        raise SnapshotError("snapshot exceeds max_total_bytes")
    digest = hashlib.sha256(data).hexdigest()
    selected[relative] = SnapshotEntry(
        path=relative,
        kind="file",
        sha256=digest,
        size_bytes=len(data),
        executable=executable,
    )
    blobs.setdefault(digest, data)
    return total_bytes


def _add_directory(
    selected: dict[str, SnapshotEntry],
    relative: str,
    *,
    limits: SnapshotLimits,
) -> bool:
    if relative in selected:
        return False
    _ensure_entry_capacity(selected, limits)
    selected[relative] = SnapshotEntry(path=relative, kind="directory")
    return True


def _process_descriptor_frame(
    stack: list[_SourceDirectoryFrame],
    selected: dict[str, SnapshotEntry],
    blobs: dict[str, bytes],
    ignored: set[str],
    patterns: tuple[str, ...],
    limits: SnapshotLimits,
    total_bytes: int,
) -> int:
    frame = stack[-1]
    if frame.index >= len(frame.names):
        _frame_must_be_stable(frame)
        if frame.parts:
            with suppress(OSError):
                os.close(frame.descriptor)
        stack.pop()
        return total_bytes

    name = frame.names[frame.index]
    frame.index += 1
    parts = (*frame.parts, name)
    relative = PurePosixPath(*parts).as_posix()
    if relative in ignored or _matches_exclude(relative, patterns):
        return total_bytes
    try:
        entry = _stat_entry(frame.descriptor, name)
    except OSError as exc:
        raise SnapshotError(f"unable to stat {relative}: {exc}") from exc
    if stat.S_ISLNK(entry.st_mode):
        raise SnapshotError(f"symbolic links are not supported: {relative}")
    if stat.S_ISDIR(entry.st_mode):
        if not _add_directory(selected, relative, limits=limits):
            return total_bytes
        child_descriptor, child_opened = _open_directory_at(
            frame.descriptor,
            name,
            relative,
            entry,
        )
        try:
            child_names = _list_directory_at(child_descriptor, relative, child_opened)
        except Exception:
            with suppress(OSError):
                os.close(child_descriptor)
            raise
        stack.append(
            _SourceDirectoryFrame(
                descriptor=child_descriptor,
                opened=child_opened,
                names=child_names,
                parts=parts,
                parent_descriptor=frame.descriptor,
                name=name,
            )
        )
        return total_bytes
    if not stat.S_ISREG(entry.st_mode):
        raise SnapshotError(f"unsupported filesystem entry: {relative}")
    if relative in selected:
        return total_bytes
    _ensure_entry_capacity(selected, limits)
    data, executable = _read_file_at(
        frame.descriptor,
        name,
        relative,
        entry,
        limits=limits,
    )
    return _add_file(
        selected,
        blobs,
        relative,
        data,
        executable,
        limits=limits,
        total_bytes=total_bytes,
    )


def _collect_descriptor(
    root: Path,
    root_info: os.stat_result,
    *,
    includes: tuple[str, ...],
    patterns: tuple[str, ...],
    ignored: set[str],
    limits: SnapshotLimits,
) -> tuple[list[SnapshotEntry], dict[str, bytes], int]:
    root_descriptor, root_opened = _open_root(root, root_info)
    selected: dict[str, SnapshotEntry] = {}
    blobs: dict[str, bytes] = {}
    total_bytes = 0
    try:
        for include in includes:
            parts = _include_parts(include)
            if not parts:
                root_names = _list_directory_at(root_descriptor, ".", root_opened)
                stack = [
                    _SourceDirectoryFrame(
                        descriptor=root_descriptor,
                        opened=root_opened,
                        names=root_names,
                        parts=(),
                    )
                ]
                while stack:
                    total_bytes = _process_descriptor_frame(
                        stack,
                        selected,
                        blobs,
                        ignored,
                        patterns,
                        limits,
                        total_bytes,
                    )
                continue

            guards = _open_guard_directories(root_descriptor, parts[:-1])
            parent_descriptor = guards[-1].descriptor if guards else root_descriptor
            relative = PurePosixPath(*parts).as_posix()
            try:
                try:
                    entry = _stat_entry(parent_descriptor, parts[-1])
                except OSError as exc:
                    raise SnapshotError(f"include path does not exist: {include}") from exc
                if relative in ignored or _matches_exclude(relative, patterns):
                    _guards_must_be_stable(guards)
                    continue
                if stat.S_ISLNK(entry.st_mode):
                    raise SnapshotError(f"symbolic links are not supported: {relative}")
                if stat.S_ISDIR(entry.st_mode):
                    if not _add_directory(selected, relative, limits=limits):
                        _guards_must_be_stable(guards)
                        continue
                    child_descriptor, child_opened = _open_directory_at(
                        parent_descriptor,
                        parts[-1],
                        relative,
                        entry,
                    )
                    try:
                        child_names = _list_directory_at(
                            child_descriptor,
                            relative,
                            child_opened,
                        )
                    except Exception:
                        with suppress(OSError):
                            os.close(child_descriptor)
                        raise
                    stack = [
                        _SourceDirectoryFrame(
                            descriptor=child_descriptor,
                            opened=child_opened,
                            names=child_names,
                            parts=parts,
                            parent_descriptor=parent_descriptor,
                            name=parts[-1],
                        )
                    ]
                    try:
                        while stack:
                            total_bytes = _process_descriptor_frame(
                                stack,
                                selected,
                                blobs,
                                ignored,
                                patterns,
                                limits,
                                total_bytes,
                            )
                    finally:
                        for frame in reversed(stack):
                            with suppress(OSError):
                                os.close(frame.descriptor)
                    _guards_must_be_stable(guards)
                    continue
                if not stat.S_ISREG(entry.st_mode):
                    raise SnapshotError(f"unsupported filesystem entry: {relative}")
                if relative not in selected:
                    _ensure_entry_capacity(selected, limits)
                    data, executable = _read_file_at(
                        parent_descriptor,
                        parts[-1],
                        relative,
                        entry,
                        limits=limits,
                    )
                    total_bytes = _add_file(
                        selected,
                        blobs,
                        relative,
                        data,
                        executable,
                        limits=limits,
                        total_bytes=total_bytes,
                    )
                _guards_must_be_stable(guards)
            finally:
                for guard in reversed(guards):
                    with suppress(OSError):
                        os.close(guard.descriptor)
        _root_descriptor_must_be_stable(root, root_descriptor, root_opened)
        entries = [selected[path] for path in sorted(selected)]
        return entries, blobs, total_bytes
    finally:
        with suppress(OSError):
            os.close(root_descriptor)


def _collect_path(
    root: Path,
    root_info: os.stat_result,
    *,
    includes: tuple[str, ...],
    patterns: tuple[str, ...],
    ignored_paths: set[Path],
    limits: SnapshotLimits,
) -> tuple[list[SnapshotEntry], dict[str, bytes], int]:
    import e2h.snapshot as snapshot

    entries: list[SnapshotEntry] = []
    blobs: dict[str, bytes] = {}
    total_bytes = 0
    for relative, path in snapshot._iter_selected(root, includes, patterns, ignored_paths):
        _root_path_contents_must_be_stable(root, root_info)
        if len(entries) >= limits.max_entries:
            raise SnapshotError("snapshot exceeds max_entries")
        snapshot._resolve_under_root(root, path, relative)
        try:
            entry = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise SnapshotError(f"unable to stat {relative}: {exc}") from exc
        if stat.S_ISLNK(entry.st_mode):
            raise SnapshotError(f"symbolic links are not supported: {relative}")
        if stat.S_ISDIR(entry.st_mode):
            entries.append(SnapshotEntry(path=relative, kind="directory"))
            _root_path_contents_must_be_stable(root, root_info)
            continue
        if not stat.S_ISREG(entry.st_mode):
            raise SnapshotError(f"unsupported filesystem entry: {relative}")
        data = snapshot._read_file(root, path, relative, limits, entry)
        _root_path_contents_must_be_stable(root, root_info)
        total_bytes += len(data)
        if total_bytes > limits.max_total_bytes:
            raise SnapshotError("snapshot exceeds max_total_bytes")
        digest = hashlib.sha256(data).hexdigest()
        entries.append(
            SnapshotEntry(
                path=relative,
                kind="file",
                sha256=digest,
                size_bytes=len(data),
                executable=bool(entry.st_mode & stat.S_IXUSR),
            )
        )
        blobs.setdefault(digest, data)
    _root_path_contents_must_be_stable(root, root_info)
    return entries, blobs, total_bytes


def collect_snapshot_source(
    root: Path,
    root_info: os.stat_result,
    *,
    includes: Iterable[str],
    excludes: Iterable[str],
    ignored_paths: set[Path],
    limits: SnapshotLimits,
) -> tuple[list[SnapshotEntry], dict[str, bytes], int]:
    """Collect one stable source-root snapshot into canonical entries and blobs."""
    includes_tuple = tuple(includes)
    patterns = tuple(excludes)
    ignored = _ignored_relatives(root, ignored_paths)
    if _SOURCE_DIR_FD_SUPPORTED:
        return _collect_descriptor(
            root,
            root_info,
            includes=includes_tuple,
            patterns=patterns,
            ignored=ignored,
            limits=limits,
        )
    return _collect_path(
        root,
        root_info,
        includes=includes_tuple,
        patterns=patterns,
        ignored_paths=ignored_paths,
        limits=limits,
    )
