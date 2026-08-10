"""Handle-bound directory helpers for replay process working directories."""

from __future__ import annotations

import os
import stat
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Iterator

_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_DIRECTORY_BINDING_SUPPORTED = (
    os.name == "posix"
    and _OPEN_SUPPORTS_DIR_FD
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


class DirectoryBindingError(RuntimeError):
    """Raised when a replay directory cannot be bound safely by handle."""


def _directory_identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _validate_relative_directory(value: str) -> str:
    if "\x00" in value:
        raise DirectoryBindingError("bound directory path must contain no NUL")
    value = value or "."
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise DirectoryBindingError(f"unsafe bound directory path: {value}")
    return value


def _directory_flags(*, nofollow: bool) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    if nofollow:
        flags |= os.O_NOFOLLOW
    return flags


def open_absolute_directory(path: Path) -> int:
    """Open one canonical absolute directory without following mutable path components."""
    if not _DIRECTORY_BINDING_SUPPORTED:
        raise DirectoryBindingError("platform does not support handle-bound replay directories")
    if "\x00" in os.fspath(path):
        raise DirectoryBindingError("bound directory path must not contain NUL")
    path = path.absolute()
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise DirectoryBindingError("bound directory path must be canonical and absolute")

    flags = _directory_flags(nofollow=True)
    anchor = Path(path.anchor)
    try:
        descriptor = os.open(anchor, flags)
    except OSError as exc:
        raise DirectoryBindingError(f"unable to bind replay directory: {exc}") from exc
    try:
        for part in path.parts[1:]:
            next_descriptor: int | None = None
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
                opened = os.fstat(next_descriptor)
                if not stat.S_ISDIR(opened.st_mode):
                    raise DirectoryBindingError("replay path component is not a directory")
            except Exception:
                if next_descriptor is not None:
                    with suppress(OSError):
                        os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as exc:
        with suppress(OSError):
            os.close(descriptor)
        raise DirectoryBindingError(f"unable to bind replay directory: {exc}") from exc
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


@contextmanager
def bound_absolute_directory(path: Path) -> Iterator[int]:
    """Yield a descriptor bound to one canonical absolute directory."""
    descriptor = open_absolute_directory(path)
    try:
        yield descriptor
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _directory_is_beneath(root_descriptor: int, child_descriptor: int) -> bool:
    root_identity = _directory_identity(os.fstat(root_descriptor))
    current_descriptor = os.dup(child_descriptor)
    try:
        while True:
            current = os.fstat(current_descriptor)
            current_identity = _directory_identity(current)
            if current_identity == root_identity:
                return True
            parent_descriptor = os.open(
                "..",
                _directory_flags(nofollow=False),
                dir_fd=current_descriptor,
            )
            try:
                parent = os.fstat(parent_descriptor)
            except Exception:
                with suppress(OSError):
                    os.close(parent_descriptor)
                raise
            if _directory_identity(parent) == current_identity:
                os.close(parent_descriptor)
                return False
            os.close(current_descriptor)
            current_descriptor = parent_descriptor
    finally:
        with suppress(OSError):
            os.close(current_descriptor)


def open_relative_directory(
    base_descriptor: int,
    relative: str,
    *,
    containment_descriptor: int,
) -> int:
    """Open a relative directory and prove its resulting handle remains under containment."""
    relative = _validate_relative_directory(relative)
    descriptor = os.open(
        relative,
        _directory_flags(nofollow=False),
        dir_fd=base_descriptor,
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise NotADirectoryError(relative)
        try:
            contained = _directory_is_beneath(containment_descriptor, descriptor)
        except OSError as exc:
            raise DirectoryBindingError(
                f"unable to validate bound workspace path {relative!r}: {exc}"
            ) from exc
        if not contained:
            raise DirectoryBindingError(f"path escapes bound workspace: {relative}")
        return descriptor
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise
