"""Descriptor-bound private snapshots for pathname-oriented DuckDB reads."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager, suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

_READ_CHUNK_BYTES = 1024 * 1024
_STORE_SUFFIXES = ("", ".wal", ".wal.checkpoint", ".wal.recovery")
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_STAT_SUPPORTS_NOFOLLOW = os.stat in os.supports_follow_symlinks
_DESCRIPTOR_BOUND_SUPPORTED = (
    _OPEN_SUPPORTS_DIR_FD
    and _STAT_SUPPORTS_DIR_FD
    and _STAT_SUPPORTS_NOFOLLOW
    and hasattr(os, "O_NOFOLLOW")
)


class StoreSnapshotError(RuntimeError):
    """Raised when a stable private store snapshot cannot be captured safely."""


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _directory_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _open_absolute_directory(path: Path) -> int:
    if not _DESCRIPTOR_BOUND_SUPPORTED:
        raise StoreSnapshotError(
            "platform does not support descriptor-bound DuckDB store snapshots"
        )
    if not path.is_absolute():
        raise StoreSnapshotError("store path must be absolute before snapshotting")
    if any(part in {".", ".."} for part in path.parts):
        raise StoreSnapshotError("store path must not contain relative path segments")

    try:
        expected = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise StoreSnapshotError(f"unable to bind store parent: {exc}") from exc
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
        raise StoreSnapshotError("unable to bind store parent: path is not a real directory")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | os.O_NOFOLLOW
    anchor = Path(path.anchor)
    try:
        descriptor = os.open(anchor, flags)
    except OSError as exc:
        raise StoreSnapshotError(f"unable to bind store parent: {exc}") from exc
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            try:
                opened = os.fstat(next_descriptor)
                if not stat.S_ISDIR(opened.st_mode):
                    raise StoreSnapshotError("store parent component is not a directory")
            except Exception:
                with suppress(OSError):
                    os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        if _directory_identity(os.fstat(descriptor)) != _directory_identity(expected):
            raise StoreSnapshotError("store parent changed while binding")
        return descriptor
    except OSError as exc:
        with suppress(OSError):
            os.close(descriptor)
        raise StoreSnapshotError(f"unable to bind store parent: {exc}") from exc
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _stat_entry(parent_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StoreSnapshotError(f"unable to inspect DuckDB store file {name!r}: {exc}") from exc


def _open_store_entry(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
) -> int:
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
        raise StoreSnapshotError(f"DuckDB store file {name!r} must be a regular file")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise StoreSnapshotError(f"unable to open DuckDB store file {name!r}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
    except OSError as exc:
        with suppress(OSError):
            os.close(descriptor)
        raise StoreSnapshotError(
            f"unable to inspect opened DuckDB store file {name!r}: {exc}"
        ) from exc
    if not stat.S_ISREG(opened.st_mode) or _stat_identity(opened) != _stat_identity(expected):
        os.close(descriptor)
        raise StoreSnapshotError(f"DuckDB store file {name!r} changed while opening")
    return descriptor


def _copy_descriptor(descriptor: int, destination: Path, expected_size: int) -> None:
    copied = 0
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as source, destination.open("xb") as target:
            while chunk := source.read(_READ_CHUNK_BYTES):
                target.write(chunk)
                copied += len(chunk)
    except OSError as exc:
        raise StoreSnapshotError(f"unable to copy DuckDB store snapshot: {exc}") from exc
    if copied != expected_size:
        raise StoreSnapshotError("DuckDB store file changed while being copied")


def _revalidate_store_set(
    parent_descriptor: int,
    names: tuple[str, ...],
    expected: dict[str, os.stat_result | None],
    opened: dict[str, int],
) -> None:
    if not stat.S_ISDIR(os.fstat(parent_descriptor).st_mode):
        raise StoreSnapshotError("DuckDB store parent is no longer a directory")
    for name in names:
        current = _stat_entry(parent_descriptor, name)
        before = expected[name]
        if before is None:
            if current is not None:
                raise StoreSnapshotError("DuckDB store sidecar set changed while snapshotting")
            continue
        descriptor = opened[name]
        after = os.fstat(descriptor)
        if current is None:
            raise StoreSnapshotError(f"DuckDB store file {name!r} changed while snapshotting")
        if (
            _stat_identity(after) != _stat_identity(before)
            or _stat_identity(current) != _stat_identity(before)
        ):
            raise StoreSnapshotError(f"DuckDB store file {name!r} changed while snapshotting")


@contextmanager
def stable_store_snapshot(database: Path) -> Iterator[Path]:
    """Yield a private pathname backed by one stable DuckDB file-set snapshot."""
    if "\x00" in os.fspath(database):
        raise StoreSnapshotError("store path must not contain NUL")
    database = database.absolute()
    if database.name in {"", ".", ".."}:
        raise StoreSnapshotError("store path must name a file without relative segments")
    parent_descriptor = _open_absolute_directory(database.parent)
    opened: dict[str, int] = {}
    try:
        parent_opened = os.fstat(parent_descriptor)
        if not stat.S_ISDIR(parent_opened.st_mode):
            raise StoreSnapshotError("store parent must be a directory")
        names = tuple(f"{database.name}{suffix}" for suffix in _STORE_SUFFIXES)
        expected = {name: _stat_entry(parent_descriptor, name) for name in names}
        if expected[names[0]] is None:
            raise StoreSnapshotError("experiment store does not exist")
        for name in names:
            entry = expected[name]
            if entry is not None:
                opened[name] = _open_store_entry(parent_descriptor, name, entry)

        with TemporaryDirectory(prefix="e2h-mcp-store-") as temporary:
            private_root = Path(temporary)
            for name in names:
                entry = expected[name]
                if entry is not None:
                    _copy_descriptor(opened[name], private_root / name, entry.st_size)
            _revalidate_store_set(
                parent_descriptor,
                names,
                expected,
                opened,
            )
            yield private_root / database.name
    finally:
        for descriptor in opened.values():
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.close(parent_descriptor)
