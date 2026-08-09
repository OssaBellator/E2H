"""Race-resistant atomic UTF-8 text publication."""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from contextlib import suppress
from pathlib import Path

_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_RENAME_SUPPORTS_DIR_FD = os.rename in os.supports_dir_fd
_UNLINK_SUPPORTS_DIR_FD = os.unlink in os.supports_dir_fd
_LISTDIR_SUPPORTS_FD = os.listdir in os.supports_fd
_ATOMIC_DIR_FD_SUPPORTED = (
    _OPEN_SUPPORTS_DIR_FD
    and _STAT_SUPPORTS_DIR_FD
    and _RENAME_SUPPORTS_DIR_FD
    and _UNLINK_SUPPORTS_DIR_FD
    and _LISTDIR_SUPPORTS_FD
)


def _inode_identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))


def _prepare_parent(path: Path) -> tuple[Path, Path, os.stat_result]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        requested_parent = path.parent.absolute()
        parent = requested_parent.resolve(strict=True)
        expected = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise OSError(f"unable to prepare atomic output parent: {exc}") from exc
    if not stat.S_ISDIR(expected.st_mode):
        raise OSError("atomic output parent must be a directory")
    return requested_parent, parent, expected


def _requested_parent_identity(requested_parent: Path) -> os.stat_result:
    try:
        current = requested_parent.resolve(strict=True)
        return current.stat(follow_symlinks=False)
    except OSError as exc:
        raise OSError(f"unable to restat atomic output parent: {exc}") from exc


def _parent_path_must_be_stable(
    requested_parent: Path,
    expected: os.stat_result,
) -> None:
    current = _requested_parent_identity(requested_parent)
    if (
        not stat.S_ISDIR(current.st_mode)
        or _directory_identity(current) != _directory_identity(expected)
    ):
        raise OSError("atomic output parent changed while writing")


def _parent_descriptor_must_be_stable(
    requested_parent: Path,
    descriptor: int,
    opened: os.stat_result,
) -> None:
    try:
        after = os.fstat(descriptor)
    except OSError as exc:
        raise OSError(f"unable to restat atomic output parent: {exc}") from exc
    current = _requested_parent_identity(requested_parent)
    expected = _directory_identity(opened)
    if (
        not stat.S_ISDIR(after.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or _directory_identity(after) != expected
        or _directory_identity(current) != expected
    ):
        raise OSError("atomic output parent changed while writing")


def _temporary_identity(descriptor: int) -> tuple[int, int]:
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        raise OSError("atomic temporary output is not a regular file")
    return _inode_identity(opened)


def _write_payload(descriptor: int, payload: str) -> None:
    expected_identity = _temporary_identity(descriptor)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="", closefd=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    after = os.fstat(descriptor)
    if not stat.S_ISREG(after.st_mode) or _inode_identity(after) != expected_identity:
        raise OSError("atomic temporary output changed while writing")


def _stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)


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
        if not stat.S_ISREG(entry.st_mode) or _inode_identity(entry) != expected_identity:
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
            continue
        finally:
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)


def _create_temporary_at(parent_descriptor: int, output_name: str) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(128):
        name = f".{output_name}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        return descriptor, name
    raise OSError("unable to allocate a unique atomic temporary output")


def _write_text_atomic_descriptor(
    requested_parent: Path,
    parent: Path,
    parent_expected: os.stat_result,
    output_name: str,
    payload: str,
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(parent, flags)
    except OSError as exc:
        raise OSError(f"unable to open atomic output parent: {exc}") from exc
    temporary_descriptor: int | None = None
    temporary_identity: tuple[int, int] | None = None
    try:
        parent_opened = os.fstat(parent_descriptor)
        if (
            not stat.S_ISDIR(parent_opened.st_mode)
            or _directory_identity(parent_opened) != _directory_identity(parent_expected)
        ):
            raise OSError("atomic output parent changed while opening")
        _parent_descriptor_must_be_stable(
            requested_parent,
            parent_descriptor,
            parent_opened,
        )
        temporary_descriptor, temporary_name = _create_temporary_at(
            parent_descriptor,
            output_name,
        )
        temporary_identity = _temporary_identity(temporary_descriptor)
        _write_payload(temporary_descriptor, payload)
        current_temporary = _stat_entry(parent_descriptor, temporary_name)
        if (
            not stat.S_ISREG(current_temporary.st_mode)
            or _inode_identity(current_temporary) != temporary_identity
        ):
            raise OSError("atomic temporary output changed before publication")
        _parent_descriptor_must_be_stable(
            requested_parent,
            parent_descriptor,
            parent_opened,
        )
        os.rename(
            temporary_name,
            output_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        current_output = _stat_entry(parent_descriptor, output_name)
        if (
            not stat.S_ISREG(current_output.st_mode)
            or _inode_identity(current_output) != temporary_identity
        ):
            raise OSError("atomic output changed during publication")
        _parent_descriptor_must_be_stable(
            requested_parent,
            parent_descriptor,
            parent_opened,
        )
        temporary_identity = None
    finally:
        if temporary_descriptor is not None:
            with suppress(OSError):
                os.close(temporary_descriptor)
        if temporary_identity is not None:
            _remove_regular_file_by_identity_at(parent_descriptor, temporary_identity)
        with suppress(OSError):
            os.close(parent_descriptor)


def _unlink_path_if_identity(path: Path, expected_identity: tuple[int, int]) -> None:
    try:
        current = path.stat(follow_symlinks=False)
    except OSError:
        return
    if not stat.S_ISREG(current.st_mode) or _inode_identity(current) != expected_identity:
        return
    with suppress(OSError):
        path.unlink()


def _write_text_atomic_path(
    requested_parent: Path,
    parent: Path,
    parent_expected: os.stat_result,
    output_name: str,
    payload: str,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_name}.",
        suffix=".tmp",
        dir=parent,
    )
    temporary_path = Path(temporary_name)
    output = parent / output_name
    temporary_identity: tuple[int, int] | None = None
    promoted = False
    try:
        temporary_identity = _temporary_identity(descriptor)
        _write_payload(descriptor, payload)
        current_temporary = temporary_path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(current_temporary.st_mode)
            or _inode_identity(current_temporary) != temporary_identity
        ):
            raise OSError("atomic temporary output changed before publication")
        _parent_path_must_be_stable(requested_parent, parent_expected)
        os.replace(temporary_path, output)
        promoted = True
        current_output = output.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(current_output.st_mode)
            or _inode_identity(current_output) != temporary_identity
        ):
            raise OSError("atomic output changed during publication")
        _parent_path_must_be_stable(requested_parent, parent_expected)
        temporary_identity = None
    finally:
        with suppress(OSError):
            os.close(descriptor)
        if temporary_identity is not None:
            _unlink_path_if_identity(temporary_path, temporary_identity)
            if promoted:
                _unlink_path_if_identity(output, temporary_identity)


def write_text_atomic(path: Path, payload: str) -> None:
    """Atomically publish UTF-8 text while binding parent and temporary identities."""
    requested_parent, parent, expected = _prepare_parent(path)
    if _ATOMIC_DIR_FD_SUPPORTED:
        _write_text_atomic_descriptor(
            requested_parent,
            parent,
            expected,
            path.name,
            payload,
        )
        return
    _write_text_atomic_path(
        requested_parent,
        parent,
        expected,
        path.name,
        payload,
    )
