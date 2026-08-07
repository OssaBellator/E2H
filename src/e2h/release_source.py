"""Deterministic identity for release source trees."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from collections.abc import Iterable
from pathlib import Path

from e2h.snapshot import (
    DEFAULT_EXCLUDES,
    SnapshotCore,
    SnapshotEntry,
    SnapshotLimits,
    snapshot_id,
)


class ReleaseSourceError(ValueError):
    """Raised when a release source tree cannot be identified safely."""


def _matches_exclude(relative: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def _hash_regular_file(
    path: Path,
    relative: str,
    *,
    limits: SnapshotLimits,
) -> tuple[str, int, bool]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to open source file {relative}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseSourceError(f"source entry is not a regular file: {relative}")
        if before.st_size > limits.max_file_bytes:
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
        stable_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_mode,
        )
        final_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_mode,
        )
        if stable_identity != final_identity or observed != before.st_size:
            raise ReleaseSourceError(f"source file changed while hashing: {relative}")
        return digest.hexdigest(), observed, bool(before.st_mode & stat.S_IXUSR)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to hash source file {relative}: {exc}") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def source_tree_sha256(
    root: Path,
    *,
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    limits: SnapshotLimits | None = None,
) -> str:
    """Return the canonical snapshot-core SHA-256 for one release source tree."""
    limits = limits or SnapshotLimits()
    try:
        if root.is_symlink() or not root.is_dir():
            raise ReleaseSourceError("release source root must be a real directory")
        root = root.resolve()
        children = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ReleaseSourceError(f"unable to inspect release source root: {exc}") from exc

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
        try:
            mode = current.stat(follow_symlinks=False).st_mode
        except OSError as exc:
            raise ReleaseSourceError(f"unable to stat source entry {relative}: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise ReleaseSourceError(f"symbolic links are not supported in release source: {relative}")
        if stat.S_ISDIR(mode):
            entries.append(SnapshotEntry(path=relative, kind="directory"))
            try:
                descendants = sorted(current.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                raise ReleaseSourceError(f"unable to list source directory {relative}: {exc}") from exc
            stack.extend(reversed(descendants))
            continue
        if not stat.S_ISREG(mode):
            raise ReleaseSourceError(f"unsupported release source entry: {relative}")
        digest, size_bytes, executable = _hash_regular_file(
            current,
            relative,
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
