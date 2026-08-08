"""Deterministic identity for release source trees."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path

from e2h.snapshot import (
    DEFAULT_EXCLUDES,
    SnapshotCore,
    SnapshotEntry,
    SnapshotLimits,
    revalidate_snapshot_limits,
    snapshot_id,
)


class ReleaseSourceError(ValueError):
    """Raised when a release source tree cannot be identified safely."""


def _matches_exclude(relative: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


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


def _list_source_directory(
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


def _hash_regular_file(
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


def source_tree_sha256(
    root: Path,
    *,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    limits: SnapshotLimits | None = None,
) -> str:
    """Return the canonical snapshot-core SHA-256 for one release source tree."""
    limits = revalidate_snapshot_limits(limits, error_type=ReleaseSourceError)
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
    root = resolved_root
    children = _list_source_directory(root, root, ".", resolved_info)

    patterns = tuple(excludes)
    stack = list(reversed(children))
    entries: list[SnapshotEntry] = []
    total_bytes = 0
    while stack:
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
            descendants = _list_source_directory(root, current, relative, entry)
            stack.extend(reversed(descendants))
            continue
        if not stat.S_ISREG(entry.st_mode):
            raise ReleaseSourceError(f"unsupported release source entry: {relative}")
        digest, size_bytes, executable = _hash_regular_file(
            root,
            current,
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

    core = SnapshotCore(
        entries=sorted(entries, key=lambda entry: entry.path),
        total_bytes=total_bytes,
    )
    return snapshot_id(core)
