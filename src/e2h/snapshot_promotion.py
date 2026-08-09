"""Post-promotion identity checks for snapshot publication and restore."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path

from e2h.snapshot import SnapshotError


def _inode_identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)


def _parent_must_be_stable(
    path: Path,
    parent_descriptor: int,
    parent_opened: os.stat_result,
    *,
    noun: str,
) -> None:
    try:
        after = os.fstat(parent_descriptor)
        current = path.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"unable to restat {noun} parent after publication: {exc}") from exc
    expected = _directory_identity(parent_opened)
    if (
        not stat.S_ISDIR(after.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or _directory_identity(after) != expected
        or _directory_identity(current) != expected
    ):
        raise SnapshotError(f"{noun} parent changed during publication")


def _remove_regular_file_by_identity_at(
    parent_descriptor: int,
    expected_identity: tuple[int, int],
) -> None:
    try:
        names = os.listdir(parent_descriptor)
    except OSError:
        return
    for name in names:
        try:
            entry = _stat_entry(parent_descriptor, name)
        except OSError:
            continue
        if _inode_identity(entry) != expected_identity or not stat.S_ISREG(entry.st_mode):
            continue
        descriptor: int | None = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
            opened = os.fstat(descriptor)
            current = _stat_entry(parent_descriptor, name)
            if (
                stat.S_ISREG(opened.st_mode)
                and _inode_identity(opened) == expected_identity
                and _inode_identity(current) == expected_identity
            ):
                os.unlink(name, dir_fd=parent_descriptor)
        except OSError:
            return
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
        return


def _open_bound_directory(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _inode_identity(opened) != _inode_identity(expected):
            raise OSError("directory identity changed while opening")
        return descriptor
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _remove_tree_at(
    parent_descriptor: int,
    name: str,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    try:
        expected = _stat_entry(parent_descriptor, name)
    except OSError:
        return
    if expected_identity is not None and _inode_identity(expected) != expected_identity:
        return
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
        return
    descriptor: int | None = None
    try:
        descriptor = _open_bound_directory(parent_descriptor, name, expected)
        for child_name in os.listdir(descriptor):
            try:
                child = _stat_entry(descriptor, child_name)
            except OSError:
                continue
            if stat.S_ISDIR(child.st_mode) and not stat.S_ISLNK(child.st_mode):
                _remove_tree_at(
                    descriptor,
                    child_name,
                    expected_identity=_inode_identity(child),
                )
            elif stat.S_ISREG(child.st_mode):
                _remove_regular_file_by_identity_at(descriptor, _inode_identity(child))
    except OSError:
        return
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    try:
        current = _stat_entry(parent_descriptor, name)
    except OSError:
        return
    if _inode_identity(current) != _inode_identity(expected):
        return
    if expected_identity is not None and _inode_identity(current) != expected_identity:
        return
    with suppress(OSError):
        os.rmdir(name, dir_fd=parent_descriptor)


def _remove_tree_by_identity_at(
    parent_descriptor: int,
    expected_identity: tuple[int, int],
) -> None:
    try:
        names = os.listdir(parent_descriptor)
    except OSError:
        return
    for name in names:
        try:
            entry = _stat_entry(parent_descriptor, name)
        except OSError:
            continue
        if _inode_identity(entry) != expected_identity:
            continue
        if stat.S_ISDIR(entry.st_mode) and not stat.S_ISLNK(entry.st_mode):
            _remove_tree_at(
                parent_descriptor,
                name,
                expected_identity=expected_identity,
            )
        return


def promote_snapshot_file(
    output: Path,
    parent_descriptor: int,
    parent_opened: os.stat_result,
    temporary_name: str,
) -> None:
    """Promote one temporary archive and bind success to its final identity."""
    try:
        temporary = _stat_entry(parent_descriptor, temporary_name)
    except OSError as exc:
        raise SnapshotError(f"unable to inspect snapshot temporary file: {exc}") from exc
    if not stat.S_ISREG(temporary.st_mode):
        raise SnapshotError("snapshot temporary file is not a regular file")
    temporary_identity = _inode_identity(temporary)
    promoted = False
    try:
        os.rename(
            temporary_name,
            output.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        promoted = True
        current = _stat_entry(parent_descriptor, output.name)
        if not stat.S_ISREG(current.st_mode) or _inode_identity(current) != temporary_identity:
            raise SnapshotError("snapshot output changed during publication")
        _parent_must_be_stable(
            output,
            parent_descriptor,
            parent_opened,
            noun="snapshot output",
        )
    except SnapshotError:
        if promoted:
            _remove_regular_file_by_identity_at(parent_descriptor, temporary_identity)
        raise
    except OSError as exc:
        if promoted:
            _remove_regular_file_by_identity_at(parent_descriptor, temporary_identity)
        raise SnapshotError(f"unable to publish snapshot output: {exc}") from exc


def promote_restore_tree(
    destination: Path,
    parent_descriptor: int,
    parent_opened: os.stat_result,
    staging_name: str,
) -> None:
    """Promote one verified restore tree and bind success to its final identity."""
    try:
        staging = _stat_entry(parent_descriptor, staging_name)
    except OSError as exc:
        raise SnapshotError(f"unable to inspect restore staging directory: {exc}") from exc
    if stat.S_ISLNK(staging.st_mode) or not stat.S_ISDIR(staging.st_mode):
        raise SnapshotError("restore staging entry is not a directory")
    staging_identity = _inode_identity(staging)
    promoted = False
    try:
        os.rename(
            staging_name,
            destination.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        promoted = True
        current = _stat_entry(parent_descriptor, destination.name)
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or _inode_identity(current) != staging_identity
        ):
            raise SnapshotError("restore destination changed during publication")
        _parent_must_be_stable(
            destination,
            parent_descriptor,
            parent_opened,
            noun="restore destination",
        )
    except SnapshotError:
        if promoted:
            _remove_tree_by_identity_at(parent_descriptor, staging_identity)
        raise
    except OSError as exc:
        if promoted:
            _remove_tree_by_identity_at(parent_descriptor, staging_identity)
        raise SnapshotError(f"unable to publish restore destination: {exc}") from exc
