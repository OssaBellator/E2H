"""Race-resistant installation for experiment-store Parquet exports."""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_LINK_SUPPORTS_DIR_FD = os.link in os.supports_dir_fd
_UNLINK_SUPPORTS_DIR_FD = os.unlink in os.supports_dir_fd
_RENAME_SUPPORTS_DIR_FD = os.rename in os.supports_dir_fd
_EXPORT_DIR_FD_SUPPORTED = (
    _OPEN_SUPPORTS_DIR_FD
    and _STAT_SUPPORTS_DIR_FD
    and _LINK_SUPPORTS_DIR_FD
    and _UNLINK_SUPPORTS_DIR_FD
)


class ParquetOutputError(ValueError):
    """Raised when a Parquet export cannot be installed safely."""


def _inode_identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _parent_must_be_stable(
    requested_parent: Path,
    descriptor: int,
    opened: os.stat_result,
) -> None:
    try:
        after = os.fstat(descriptor)
        current_parent = requested_parent.resolve(strict=True)
        current = current_parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise ParquetOutputError(f"unable to restat Parquet output parent: {exc}") from exc
    expected_identity = _inode_identity(opened)
    if (
        not stat.S_ISDIR(after.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or _inode_identity(after) != expected_identity
        or _inode_identity(current) != expected_identity
    ):
        raise ParquetOutputError("Parquet output parent changed while exporting")


def _stat_entry(parent_descriptor: int, parent: Path, name: str) -> os.stat_result:
    if _EXPORT_DIR_FD_SUPPORTED:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    return (parent / name).stat(follow_symlinks=False)


def _open_entry(
    parent_descriptor: int,
    parent: Path,
    name: str,
    flags: int,
    mode: int | None = None,
) -> int:
    if _EXPORT_DIR_FD_SUPPORTED:
        if mode is None:
            return os.open(name, flags, dir_fd=parent_descriptor)
        return os.open(name, flags, mode, dir_fd=parent_descriptor)
    if mode is None:
        return os.open(parent / name, flags)
    return os.open(parent / name, flags, mode)


def _unlink_entry(parent_descriptor: int, parent: Path, name: str) -> None:
    if _EXPORT_DIR_FD_SUPPORTED:
        os.unlink(name, dir_fd=parent_descriptor)
    else:
        (parent / name).unlink()


def _link_entry(
    parent_descriptor: int,
    parent: Path,
    source_name: str,
    destination_name: str,
) -> None:
    if _EXPORT_DIR_FD_SUPPORTED:
        os.link(
            source_name,
            destination_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    else:
        os.link(
            parent / source_name,
            parent / destination_name,
            follow_symlinks=False,
        )


def _replace_entry(
    parent_descriptor: int,
    parent: Path,
    source_name: str,
    destination_name: str,
) -> None:
    if _RENAME_SUPPORTS_DIR_FD:
        os.rename(
            source_name,
            destination_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
    else:
        os.replace(parent / source_name, parent / destination_name)


def _unlink_regular_entry_if_identity(
    parent_descriptor: int,
    parent: Path,
    name: str,
    expected_identity: tuple[int, int],
) -> None:
    try:
        current = _stat_entry(parent_descriptor, parent, name)
    except OSError:
        return
    if not stat.S_ISREG(current.st_mode) or _inode_identity(current) != expected_identity:
        return
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = _open_entry(parent_descriptor, parent, name, flags)
        opened = os.fstat(descriptor)
        current = _stat_entry(parent_descriptor, parent, name)
        if (
            stat.S_ISREG(opened.st_mode)
            and _inode_identity(opened) == expected_identity
            and _inode_identity(current) == expected_identity
        ):
            _unlink_entry(parent_descriptor, parent, name)
    except OSError:
        return
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _copy_staged(staged: Path, descriptor: int) -> os.stat_result:
    observed = 0
    os.ftruncate(descriptor, 0)
    os.lseek(descriptor, 0, os.SEEK_SET)
    with staged.open("rb") as source, os.fdopen(descriptor, "wb", closefd=False) as target:
        while chunk := source.read(1024 * 1024):
            target.write(chunk)
            observed += len(chunk)
        target.flush()
    os.fsync(descriptor)
    after = os.fstat(descriptor)
    if after.st_size != observed:
        raise ParquetOutputError("Parquet output changed while writing")
    return after


def _install_new(
    parent: Path,
    requested_parent: Path,
    parent_descriptor: int,
    parent_opened: os.stat_result,
    output_name: str,
    staged: Path,
) -> None:
    temp_name = f".{output_name}.e2h-{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    opened: os.stat_result | None = None
    current: os.stat_result | None = None
    installed = False
    success = False
    try:
        descriptor = _open_entry(
            parent_descriptor,
            parent,
            temp_name,
            flags,
            0o666,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ParquetOutputError("temporary Parquet output is not a regular file")
        written = _copy_staged(staged, descriptor)
        if _inode_identity(written) != _inode_identity(opened):
            raise ParquetOutputError("temporary Parquet output changed while writing")
        try:
            current_temporary = _stat_entry(parent_descriptor, parent, temp_name)
        except OSError as exc:
            raise ParquetOutputError(
                f"unable to restat temporary Parquet output: {exc}"
            ) from exc
        if not stat.S_ISREG(current_temporary.st_mode) or _inode_identity(
            current_temporary
        ) != _inode_identity(opened):
            raise ParquetOutputError("temporary Parquet output changed before installation")
        _parent_must_be_stable(requested_parent, parent_descriptor, parent_opened)
        try:
            _link_entry(
                parent_descriptor,
                parent,
                temp_name,
                output_name,
            )
        except FileExistsError as exc:
            raise ParquetOutputError("Parquet output destination appeared while exporting") from exc
        installed = True
        current = _stat_entry(parent_descriptor, parent, output_name)
        if _inode_identity(current) != _inode_identity(written):
            raise ParquetOutputError("Parquet output destination changed while installing")
        _parent_must_be_stable(requested_parent, parent_descriptor, parent_opened)
        success = True
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if installed and not success and opened is not None:
            try:
                current = _stat_entry(parent_descriptor, parent, output_name)
            except OSError:
                current = None
            if current is not None and _inode_identity(current) == _inode_identity(opened):
                with suppress(OSError):
                    _unlink_entry(parent_descriptor, parent, output_name)
        if opened is not None:
            _unlink_regular_entry_if_identity(
                parent_descriptor,
                parent,
                temp_name,
                _inode_identity(opened),
            )


def _create_rollback_link(
    parent: Path,
    parent_descriptor: int,
    output_name: str,
    expected_identity: tuple[int, int],
) -> str:
    for _ in range(128):
        backup_name = f".{output_name}.e2h-rollback-{secrets.token_hex(16)}.bak"
        try:
            _link_entry(
                parent_descriptor,
                parent,
                output_name,
                backup_name,
            )
        except FileExistsError:
            continue
        except OSError as exc:
            raise ParquetOutputError(f"unable to preserve existing Parquet output: {exc}") from exc
        try:
            backup = _stat_entry(parent_descriptor, parent, backup_name)
            current = _stat_entry(parent_descriptor, parent, output_name)
        except OSError as exc:
            _unlink_regular_entry_if_identity(
                parent_descriptor,
                parent,
                backup_name,
                expected_identity,
            )
            raise ParquetOutputError(f"unable to verify preserved Parquet output: {exc}") from exc
        if (
            stat.S_ISREG(backup.st_mode)
            and stat.S_ISREG(current.st_mode)
            and _inode_identity(backup) == expected_identity
            and _inode_identity(current) == expected_identity
        ):
            return backup_name
        _unlink_regular_entry_if_identity(
            parent_descriptor,
            parent,
            backup_name,
            expected_identity,
        )
        raise ParquetOutputError("Parquet output destination changed while preserving it")
    raise ParquetOutputError("unable to allocate Parquet output rollback entry")


def _restore_overwritten_output(
    parent: Path,
    parent_descriptor: int,
    output_name: str,
    backup_name: str,
    backup_identity: tuple[int, int],
    replacement_identity: tuple[int, int],
) -> None:
    try:
        backup = _stat_entry(parent_descriptor, parent, backup_name)
        current = _stat_entry(parent_descriptor, parent, output_name)
    except OSError:
        return
    if (
        not stat.S_ISREG(backup.st_mode)
        or _inode_identity(backup) != backup_identity
        or not stat.S_ISREG(current.st_mode)
        or _inode_identity(current) != replacement_identity
    ):
        return
    with suppress(OSError):
        _replace_entry(
            parent_descriptor,
            parent,
            backup_name,
            output_name,
        )


def _overwrite_existing(
    parent: Path,
    requested_parent: Path,
    parent_descriptor: int,
    parent_opened: os.stat_result,
    output_name: str,
    expected: os.stat_result,
    staged: Path,
) -> None:
    temp_name = f".{output_name}.e2h-{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    opened: os.stat_result | None = None
    backup_name: str | None = None
    success = False
    try:
        descriptor = _open_entry(
            parent_descriptor,
            parent,
            temp_name,
            flags,
            0o666,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ParquetOutputError("temporary Parquet output is not a regular file")
        written = _copy_staged(staged, descriptor)
        replacement_identity = _inode_identity(opened)
        if _inode_identity(written) != replacement_identity:
            raise ParquetOutputError("temporary Parquet output changed while writing")
        current_temporary = _stat_entry(parent_descriptor, parent, temp_name)
        if (
            not stat.S_ISREG(current_temporary.st_mode)
            or _inode_identity(current_temporary) != replacement_identity
        ):
            raise ParquetOutputError("temporary Parquet output changed before installation")
        current_output = _stat_entry(parent_descriptor, parent, output_name)
        expected_identity = _inode_identity(expected)
        if (
            not stat.S_ISREG(current_output.st_mode)
            or _inode_identity(current_output) != expected_identity
        ):
            raise ParquetOutputError("Parquet output destination changed before overwrite")
        _parent_must_be_stable(requested_parent, parent_descriptor, parent_opened)
        backup_name = _create_rollback_link(
            parent,
            parent_descriptor,
            output_name,
            expected_identity,
        )
        _replace_entry(
            parent_descriptor,
            parent,
            temp_name,
            output_name,
        )
        current_output = _stat_entry(parent_descriptor, parent, output_name)
        if (
            not stat.S_ISREG(current_output.st_mode)
            or _inode_identity(current_output) != replacement_identity
        ):
            raise ParquetOutputError("Parquet output destination changed while overwriting")
        _parent_must_be_stable(requested_parent, parent_descriptor, parent_opened)
        success = True
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if opened is not None:
            replacement_identity = _inode_identity(opened)
            if not success and backup_name is not None:
                _restore_overwritten_output(
                    parent,
                    parent_descriptor,
                    output_name,
                    backup_name,
                    _inode_identity(expected),
                    replacement_identity,
                )
            _unlink_regular_entry_if_identity(
                parent_descriptor,
                parent,
                temp_name,
                replacement_identity,
            )
        if backup_name is not None:
            _unlink_regular_entry_if_identity(
                parent_descriptor,
                parent,
                backup_name,
                _inode_identity(expected),
            )


def _install_staged(output: Path, staged: Path) -> None:
    if not output.name:
        raise ParquetOutputError("Parquet output must name a file")
    requested_parent = output.parent.absolute()
    requested_parent.mkdir(parents=True, exist_ok=True)
    try:
        parent = requested_parent.resolve(strict=True)
        parent_expected = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise ParquetOutputError(f"unable to inspect Parquet output parent: {exc}") from exc
    if stat.S_ISLNK(parent_expected.st_mode) or not stat.S_ISDIR(parent_expected.st_mode):
        raise ParquetOutputError("Parquet output parent must be a real directory")
    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(parent, parent_flags)
    except OSError as exc:
        raise ParquetOutputError(f"unable to open Parquet output parent: {exc}") from exc
    try:
        parent_opened = os.fstat(parent_descriptor)
        if not stat.S_ISDIR(parent_opened.st_mode) or _inode_identity(
            parent_opened
        ) != _inode_identity(parent_expected):
            raise ParquetOutputError("Parquet output parent changed while opening")
        expected: os.stat_result | None
        try:
            expected = _stat_entry(parent_descriptor, parent, output.name)
        except FileNotFoundError:
            expected = None
        except OSError as exc:
            raise ParquetOutputError(
                f"unable to inspect Parquet output destination: {exc}"
            ) from exc
        if expected is not None and (
            stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode)
        ):
            raise ParquetOutputError("Parquet output destination must be a regular file")
        _parent_must_be_stable(requested_parent, parent_descriptor, parent_opened)
        if expected is None:
            _install_new(
                parent,
                requested_parent,
                parent_descriptor,
                parent_opened,
                output.name,
                staged,
            )
        else:
            _overwrite_existing(
                parent,
                requested_parent,
                parent_descriptor,
                parent_opened,
                output.name,
                expected,
                staged,
            )
    finally:
        with suppress(OSError):
            os.close(parent_descriptor)


@contextmanager
def staged_parquet_output(output: Path) -> Iterator[Path]:
    """Yield a private staging path and safely install it on successful exit."""
    try:
        with tempfile.TemporaryDirectory(prefix="e2h-store-export-") as staging_directory:
            staged = Path(staging_directory) / "export.parquet"
            yield staged
            if not staged.is_file():
                raise ParquetOutputError("Parquet export did not produce a regular staging file")
            _install_staged(output, staged)
    except ParquetOutputError:
        raise
    except OSError as exc:
        raise ParquetOutputError(f"unable to install Parquet export: {exc}") from exc
