"""Deterministic content-addressed workspace snapshot bundles."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import secrets
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SNAPSHOT_SCHEMA_VERSION = "0.1"
MANIFEST_NAME = "manifest.json"
BLOB_PREFIX = "blobs/"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 10_000
DEFAULT_MAX_FILE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 1024 * 1024 * 1024
DEFAULT_EXCLUDES = (
    ".git",
    ".git/**",
    ".venv",
    ".venv/**",
    ".e2h",
    ".e2h/**",
    "__pycache__",
    "__pycache__/**",
    "**/__pycache__",
    "**/__pycache__/**",
    "*.pyc",
    "**/*.pyc",
)
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_LISTDIR_SUPPORTS_FD = os.listdir in os.supports_fd
_MKDIR_SUPPORTS_DIR_FD = os.mkdir in os.supports_dir_fd
_RENAME_SUPPORTS_DIR_FD = os.rename in os.supports_dir_fd
_RMDIR_SUPPORTS_DIR_FD = os.rmdir in os.supports_dir_fd
_UNLINK_SUPPORTS_DIR_FD = os.unlink in os.supports_dir_fd
_CLEANUP_DIR_FD_SUPPORTED = (
    _OPEN_SUPPORTS_DIR_FD
    and _STAT_SUPPORTS_DIR_FD
    and _LISTDIR_SUPPORTS_FD
    and _RMDIR_SUPPORTS_DIR_FD
    and _UNLINK_SUPPORTS_DIR_FD
)
_WRITE_DIR_FD_SUPPORTED = (
    _CLEANUP_DIR_FD_SUPPORTED
    and _MKDIR_SUPPORTS_DIR_FD
    and _RENAME_SUPPORTS_DIR_FD
)
_PLATFORM_PATH_TYPE = type(Path())


class SnapshotError(ValueError):
    """Raised when snapshot creation, verification, or restoration fails."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_json_object_keys(value: Any, *, active: set[int] | None = None) -> None:
    if active is None:
        active = set()
    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            raise ValueError("JSON value contains a recursive container")
        active.add(identity)
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("JSON objects must use string keys")
                _validate_json_object_keys(item, active=active)
        finally:
            active.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("JSON value contains a recursive container")
        active.add(identity)
        try:
            for item in value:
                _validate_json_object_keys(item, active=active)
        finally:
            active.remove(identity)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key!r}")
        result[key] = value
    return result


def _canonical_json(value: Any) -> bytes:
    try:
        _validate_json_object_keys(value)
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"value is not canonical JSON: {exc}") from exc
    return rendered.encode("utf-8")


def _safe_relative_path(value: str) -> str:
    if not value or value == ".":
        raise ValueError("snapshot entry path must identify a child of the root")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("snapshot entry path must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("snapshot entry path contains an unsafe segment")
    return value


class SnapshotEntry(StrictModel):
    """One directory or regular file represented by a snapshot."""

    path: str
    kind: Literal["directory", "file"]
    sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    size_bytes: int = Field(default=0, ge=0)
    executable: bool = False

    @field_validator("path")
    @classmethod
    def path_must_be_safe(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def fields_must_match_kind(self) -> SnapshotEntry:
        if self.kind == "directory" and (
            self.sha256 is not None or self.size_bytes != 0 or self.executable
        ):
            raise ValueError("directory entries cannot declare file attributes")
        if self.kind == "file" and self.sha256 is None:
            raise ValueError("file entries require sha256")
        return self


class SnapshotCore(StrictModel):
    """Immutable content-addressed portion of a snapshot manifest."""

    schema_version: Literal["0.1"] = "0.1"
    entries: list[SnapshotEntry] = Field(max_length=DEFAULT_MAX_ENTRIES)
    total_bytes: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def entries_must_be_consistent(self) -> SnapshotCore:
        try:
            metadata_size = len(_canonical_json(self.metadata))
        except SnapshotError as exc:
            raise ValueError(str(exc)) from exc
        if metadata_size > MAX_MANIFEST_BYTES // 2:
            raise ValueError("snapshot metadata is too large")
        paths = [entry.path for entry in self.entries]
        if paths != sorted(paths):
            raise ValueError("snapshot entries must be sorted by path")
        if len(paths) != len(set(paths)):
            raise ValueError("snapshot entry paths must be unique")
        known = {entry.path: entry.kind for entry in self.entries}
        for entry in self.entries:
            parent = PurePosixPath(entry.path).parent
            while str(parent) != ".":
                parent_kind = known.get(parent.as_posix())
                if parent_kind == "file":
                    raise ValueError(f"file entry cannot contain child paths: {parent}")
                parent = parent.parent
        digest_sizes: dict[str, int] = {}
        for entry in self.entries:
            if entry.kind != "file" or entry.sha256 is None:
                continue
            previous_size = digest_sizes.setdefault(entry.sha256, entry.size_bytes)
            if previous_size != entry.size_bytes:
                raise ValueError("file entries sharing sha256 must declare the same size_bytes")
        file_bytes = sum(entry.size_bytes for entry in self.entries if entry.kind == "file")
        if file_bytes != self.total_bytes:
            raise ValueError("total_bytes does not match file entries")
        return self


class SnapshotManifest(StrictModel):
    """Verified manifest stored inside a deterministic snapshot archive."""

    snapshot_id: str = Field(pattern=_SHA256_PATTERN)
    core: SnapshotCore

    @model_validator(mode="after")
    def identifier_must_match_core(self) -> SnapshotManifest:
        if self.snapshot_id != _snapshot_id_validated(self.core):
            raise ValueError("snapshot_id does not match the manifest core")
        return self


class SnapshotReference(StrictModel):
    """Portable reference that can be attached to a compiled capsule."""

    snapshot_id: str = Field(pattern=_SHA256_PATTERN)
    archive_sha256: str = Field(pattern=_SHA256_PATTERN)
    locator: str = Field(min_length=1, max_length=2_000)
    role: Literal["workspace", "artifact"] = "workspace"


class SnapshotLimits(StrictModel):
    """Resource limits applied while creating or verifying a snapshot."""

    max_entries: int = Field(default=DEFAULT_MAX_ENTRIES, ge=1, le=DEFAULT_MAX_ENTRIES)
    max_file_bytes: int = Field(default=DEFAULT_MAX_FILE_BYTES, ge=1)
    max_total_bytes: int = Field(default=DEFAULT_MAX_TOTAL_BYTES, ge=1)


def revalidate_snapshot_limits(
    limits: SnapshotLimits | None,
    *,
    error_type: type[ValueError] = SnapshotError,
) -> SnapshotLimits:
    """Return a detached limits snapshot before any bounded filesystem work."""
    if limits is None:
        return SnapshotLimits()
    if type(limits) is not SnapshotLimits:
        raise error_type(
            f"invalid snapshot limits: expected SnapshotLimits, got {type(limits).__name__}"
        )
    try:
        payload = limits.model_dump(mode="python", warnings="none")
        return SnapshotLimits.model_validate(payload)
    except ValueError as exc:
        raise error_type(f"invalid snapshot limits: {exc}") from exc


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _inode_identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode)


def _requested_snapshot_parent_must_be_stable(
    requested_parent: Path,
    expected: os.stat_result,
    *,
    noun: str,
    phase: str,
    full_identity: bool,
) -> None:
    try:
        current_parent = requested_parent.resolve(strict=True)
        current = current_parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"unable to restat {noun} parent: {exc}") from exc
    if not stat.S_ISDIR(current.st_mode):
        raise SnapshotError(f"{noun} parent changed while {phase}")
    stable = (
        _stat_identity(current) == _stat_identity(expected)
        if full_identity
        else _directory_identity(current) == _directory_identity(expected)
    )
    if not stable:
        raise SnapshotError(f"{noun} parent changed while {phase}")


def _bound_snapshot_parent_stat(
    self: Any,
    *,
    follow_symlinks: bool = True,
) -> os.stat_result:
    current = Path(self).stat(follow_symlinks=follow_symlinks)
    _requested_snapshot_parent_must_be_stable(
        self.requested_parent,
        self.expected_parent,
        noun=self.noun,
        phase="writing",
        full_identity=False,
    )
    return current


_BOUND_SNAPSHOT_PARENT_TYPE = type(
    "_BoundSnapshotParent",
    (_PLATFORM_PATH_TYPE,),
    {
        "__slots__": ("requested_parent", "expected_parent", "noun"),
        "stat": _bound_snapshot_parent_stat,
    },
)


def _bound_snapshot_path_parent(self: Any) -> Path:
    parent = _BOUND_SNAPSHOT_PARENT_TYPE(Path(self).parent)
    parent.requested_parent = self.requested_parent
    parent.expected_parent = self.expected_parent
    parent.noun = self.noun
    return parent


_BOUND_SNAPSHOT_PATH_TYPE = type(
    "_BoundSnapshotPath",
    (_PLATFORM_PATH_TYPE,),
    {
        "__slots__": ("requested_parent", "expected_parent", "noun"),
        "parent": property(_bound_snapshot_path_parent),
    },
)


def _bind_snapshot_path(
    path: Path,
    requested_parent: Path,
    expected_parent: os.stat_result,
    *,
    noun: str,
) -> Path:
    bound = _BOUND_SNAPSHOT_PATH_TYPE(path)
    bound.requested_parent = requested_parent
    bound.expected_parent = expected_parent
    bound.noun = noun
    return bound


def _validated_snapshot_core(core: SnapshotCore) -> SnapshotCore:
    if type(core) is not SnapshotCore:
        raise SnapshotError(
            f"invalid snapshot core: expected SnapshotCore, got {type(core).__name__}"
        )
    try:
        payload = core.model_dump(mode="python", warnings="none")
        return SnapshotCore.model_validate(payload)
    except ValueError as exc:
        raise SnapshotError(f"invalid snapshot core: {exc}") from exc


def _snapshot_id_validated(core: SnapshotCore) -> str:
    payload = _canonical_json(core.model_dump(mode="json"))
    return hashlib.sha256(payload).hexdigest()


def snapshot_id(core: SnapshotCore) -> str:
    return _snapshot_id_validated(_validated_snapshot_core(core))


def _open_snapshot_write_parent(
    path: Path,
    *,
    noun: str,
) -> tuple[Path, int, os.stat_result]:
    requested_parent = path.parent.absolute()
    try:
        requested_parent.mkdir(parents=True, exist_ok=True)
        parent = requested_parent.resolve(strict=True)
        expected = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"unable to prepare {noun}: {exc}") from exc
    if not stat.S_ISDIR(expected.st_mode):
        raise SnapshotError(f"{noun} parent must be a directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(parent, flags)
    except OSError as exc:
        raise SnapshotError(f"unable to open {noun} parent: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise SnapshotError(f"{noun} parent is no longer a directory")
        if _stat_identity(opened) != _stat_identity(expected):
            raise SnapshotError(f"{noun} parent changed while opening")
        _requested_snapshot_parent_must_be_stable(
            requested_parent,
            opened,
            noun=noun,
            phase="opening",
            full_identity=True,
        )
        bound_path = _bind_snapshot_path(
            parent / path.name,
            requested_parent,
            opened,
            noun=noun,
        )
        return bound_path, descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _snapshot_write_parent_must_be_stable(
    path: Path,
    descriptor: int,
    opened: os.stat_result,
    *,
    noun: str,
) -> None:
    try:
        after = os.fstat(descriptor)
        current = path.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"unable to restat {noun} parent: {exc}") from exc
    if _directory_identity(after) != _directory_identity(opened) or _directory_identity(
        current
    ) != _directory_identity(opened):
        raise SnapshotError(f"{noun} parent changed while writing")


def _stat_snapshot_write_entry(parent_descriptor: int, path: Path) -> os.stat_result:
    if _STAT_SUPPORTS_DIR_FD:
        return os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
    return path.stat(follow_symlinks=False)


def _directory_entry_is_empty(
    parent_descriptor: int,
    path: Path,
    expected: os.stat_result,
    *,
    noun: str,
) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = (
            os.open(path.name, flags, dir_fd=parent_descriptor)
            if _OPEN_SUPPORTS_DIR_FD
            else os.open(path, flags)
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _stat_identity(opened) != _stat_identity(expected):
            raise SnapshotError(f"{noun} changed while opening")
        names = os.listdir(descriptor) if _LISTDIR_SUPPORTS_FD else os.listdir(path)
        after = os.fstat(descriptor)
        current = _stat_snapshot_write_entry(parent_descriptor, path)
        if _stat_identity(after) != _stat_identity(opened) or _stat_identity(
            current
        ) != _stat_identity(opened):
            raise SnapshotError(f"{noun} changed while inspecting")
        return not names
    except OSError as exc:
        raise SnapshotError(f"unable to inspect {noun}: {exc}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _restore_destination_is_empty(parent_descriptor: int, destination: Path) -> bool:
    try:
        expected = _stat_snapshot_write_entry(parent_descriptor, destination)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SnapshotError(f"unable to inspect restore destination: {exc}") from exc
    if stat.S_ISLNK(expected.st_mode):
        raise SnapshotError("restore destination must not be a symbolic link")
    if not stat.S_ISDIR(expected.st_mode):
        raise SnapshotError("restore destination exists and is not a directory")
    if not _directory_entry_is_empty(
        parent_descriptor,
        destination,
        expected,
        noun="restore destination",
    ):
        raise SnapshotError("restore destination must be empty")
    return True


def _create_snapshot_temporary_file(
    parent_descriptor: int,
    output: Path,
) -> tuple[int, str, Path]:
    if not _WRITE_DIR_FD_SUPPORTED:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
        )
        temporary = Path(temporary_name)
        return descriptor, temporary.name, temporary
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _ in range(128):
        name = f".{output.name}.{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        return descriptor, name, output.parent / name
    raise SnapshotError("unable to allocate a unique snapshot temporary file")


def _remove_regular_path_if_identity(path: Path, expected_identity: tuple[int, int]) -> bool:
    descriptor: int | None = None
    try:
        current = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or _inode_identity(current) != expected_identity:
            return False
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _inode_identity(opened) != expected_identity
            or _inode_identity(current) != expected_identity
        ):
            return False
        path.unlink()
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)


def _backup_snapshot_output_path(
    output: Path,
    expected_identity: tuple[int, int] | None,
) -> tuple[Path, tuple[int, int]] | None:
    if expected_identity is None:
        return None
    plain_output = Path(output)
    for _ in range(128):
        backup = plain_output.parent / f".{plain_output.name}.rollback-{secrets.token_hex(16)}.bak"
        try:
            backup.stat(follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SnapshotError(f"unable to inspect snapshot output rollback path: {exc}") from exc
        else:
            continue
        try:
            current = plain_output.stat(follow_symlinks=False)
        except OSError as exc:
            raise SnapshotError(f"unable to preserve existing snapshot output: {exc}") from exc
        if not stat.S_ISREG(current.st_mode) or _inode_identity(current) != expected_identity:
            raise SnapshotError("snapshot output changed before publication")
        try:
            os.replace(plain_output, backup)
            moved = backup.stat(follow_symlinks=False)
        except OSError as exc:
            raise SnapshotError(f"unable to preserve existing snapshot output: {exc}") from exc
        if stat.S_ISREG(moved.st_mode) and _inode_identity(moved) == expected_identity:
            return backup, expected_identity
        try:
            plain_output.stat(follow_symlinks=False)
        except FileNotFoundError:
            with suppress(OSError):
                os.replace(backup, plain_output)
        raise SnapshotError("snapshot output changed while preserving it")
    raise SnapshotError("unable to allocate snapshot output rollback path")


def _restore_snapshot_output_path(
    output: Path,
    backup: tuple[Path, tuple[int, int]],
    promoted_identity: tuple[int, int] | None,
) -> bool:
    backup_path, backup_identity = backup
    plain_output = Path(output)
    try:
        backup_current = backup_path.stat(follow_symlinks=False)
    except OSError:
        return False
    if (
        not stat.S_ISREG(backup_current.st_mode)
        or _inode_identity(backup_current) != backup_identity
    ):
        return False
    try:
        output_current = plain_output.stat(follow_symlinks=False)
    except FileNotFoundError:
        output_current = None
    except OSError:
        return False
    if output_current is not None:
        if not stat.S_ISREG(output_current.st_mode):
            return False
        current_identity = _inode_identity(output_current)
        if current_identity == backup_identity:
            _remove_regular_path_if_identity(backup_path, backup_identity)
            return True
        if promoted_identity is None or current_identity != promoted_identity:
            return False
    try:
        os.replace(backup_path, plain_output)
        restored = plain_output.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(restored.st_mode) and _inode_identity(restored) == backup_identity


def _create_restore_staging_directory(
    parent_descriptor: int,
    destination: Path,
) -> tuple[str, Path]:
    if not _WRITE_DIR_FD_SUPPORTED:
        staging = Path(
            tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent)
        ).resolve()
        return staging.name, staging
    for _ in range(128):
        name = f".{destination.name}.restore-{secrets.token_hex(16)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
        except FileExistsError:
            continue
        return name, destination.parent / name
    raise SnapshotError("unable to allocate a unique restore staging directory")


def _open_bound_child_directory(
    parent_descriptor: int,
    name: str,
    expected: os.stat_result,
    *,
    noun: str,
) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise SnapshotError(f"unable to open {noun}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _stat_identity(opened) != _stat_identity(expected):
            raise SnapshotError(f"{noun} changed while opening")
        return descriptor
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _open_restore_directory(root_descriptor: int, parts: tuple[str, ...]) -> int:
    current = os.dup(root_descriptor)
    try:
        for part in parts:
            try:
                expected = os.stat(part, dir_fd=current, follow_symlinks=False)
            except FileNotFoundError:
                os.mkdir(part, 0o777, dir_fd=current)
                expected = os.stat(part, dir_fd=current, follow_symlinks=False)
            if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
                raise SnapshotError(f"restore path component must be a directory: {part}")
            child = _open_bound_child_directory(
                current,
                part,
                expected,
                noun=f"restore directory {part}",
            )
            os.close(current)
            current = child
        return current
    except Exception:
        with suppress(OSError):
            os.close(current)
        raise


def _write_restore_file_at(
    staging_descriptor: int,
    parts: tuple[str, ...],
    data: bytes,
    *,
    executable: bool,
) -> None:
    parent_descriptor = _open_restore_directory(staging_descriptor, parts[:-1])
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(parts[-1], flags, 0o600, dir_fd=parent_descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SnapshotError("restore target is not a regular file")
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
        after = os.fstat(descriptor)
        if not stat.S_ISREG(after.st_mode) or after.st_size != len(data):
            raise SnapshotError("restore target changed while writing")
        os.fchmod(descriptor, 0o755 if executable else 0o644)
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.close(parent_descriptor)


def _remove_restore_tree_at(
    parent_descriptor: int,
    name: str,
    path: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    if not _CLEANUP_DIR_FD_SUPPORTED:
        if expected_identity is None:
            shutil.rmtree(path, ignore_errors=True)
        return
    from e2h.snapshot_promotion import _remove_tree_by_identity_at

    if expected_identity is None:
        try:
            expected = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError:
            return
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
            return
        expected_identity = _inode_identity(expected)
    _remove_tree_by_identity_at(parent_descriptor, expected_identity)


@contextmanager
def _open_snapshot_archive_file(path: Path) -> Iterator[BinaryIO]:
    requested_parent = path.parent.absolute()
    try:
        parent = requested_parent.resolve(strict=True)
        parent_expected = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError(f"unable to open snapshot archive: {exc}") from exc
    if not stat.S_ISDIR(parent_expected.st_mode):
        raise SnapshotError("snapshot archive parent must be a directory")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(parent, directory_flags)
    except OSError as exc:
        raise SnapshotError(f"unable to open snapshot archive: {exc}") from exc
    descriptor: int | None = None
    handle: BinaryIO | None = None
    try:
        parent_opened = os.fstat(parent_descriptor)
        if _stat_identity(parent_opened) != _stat_identity(parent_expected):
            raise SnapshotError("snapshot archive parent changed while opening")
        _requested_snapshot_parent_must_be_stable(
            requested_parent,
            parent_opened,
            noun="snapshot archive",
            phase="opening",
            full_identity=True,
        )
        try:
            expected = (
                os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if _STAT_SUPPORTS_DIR_FD
                else (parent / path.name).stat(follow_symlinks=False)
            )
        except OSError as exc:
            raise SnapshotError(f"unable to open snapshot archive: {exc}") from exc
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
            raise SnapshotError("snapshot archive must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = (
                os.open(path.name, flags, dir_fd=parent_descriptor)
                if _OPEN_SUPPORTS_DIR_FD
                else os.open(parent / path.name, flags)
            )
        except OSError as exc:
            raise SnapshotError(f"unable to open snapshot archive: {exc}") from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SnapshotError("snapshot archive must be a regular file")
        if _stat_identity(opened) != _stat_identity(expected):
            raise SnapshotError("snapshot archive changed while opening")
        handle = os.fdopen(descriptor, "rb", closefd=False)
        try:
            yield handle
        except Exception:
            raise
        else:
            after = os.fstat(descriptor)
            current = (
                os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if _STAT_SUPPORTS_DIR_FD
                else (parent / path.name).stat(follow_symlinks=False)
            )
            parent_after = os.fstat(parent_descriptor)
            parent_current = parent.stat(follow_symlinks=False)
            if _stat_identity(after) != _stat_identity(opened) or _stat_identity(
                current
            ) != _stat_identity(opened):
                raise SnapshotError("snapshot archive changed while reading")
            if _stat_identity(parent_after) != _stat_identity(parent_opened) or _stat_identity(
                parent_current
            ) != _stat_identity(parent_opened):
                raise SnapshotError("snapshot archive parent changed while reading")
            _requested_snapshot_parent_must_be_stable(
                requested_parent,
                parent_opened,
                noun="snapshot archive",
                phase="reading",
                full_identity=True,
            )
    except OSError as exc:
        raise SnapshotError(f"unable to read snapshot archive: {exc}") from exc
    finally:
        if handle is not None:
            with suppress(OSError):
                handle.close()
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.close(parent_descriptor)


def _archive_sha256_handle(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    try:
        handle.seek(0)
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
        handle.seek(0)
    except OSError as exc:
        raise SnapshotError(f"unable to hash snapshot archive: {exc}") from exc
    return digest.hexdigest()


def archive_sha256(path: Path) -> str:
    with _open_snapshot_archive_file(path) as handle:
        return _archive_sha256_handle(handle)


def _relative(root: Path, candidate: Path) -> str:
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise SnapshotError(f"path is outside snapshot root: {candidate}") from exc


def _matches_exclude(relative: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in patterns)


def _resolve_selected(root: Path, value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise SnapshotError(f"include path must be a safe relative path: {value}")
    candidate = root / Path(*pure.parts)
    try:
        candidate.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise SnapshotError(f"include path does not exist: {value}") from exc
    except OSError as exc:
        raise SnapshotError(f"unable to stat include path {value}: {exc}") from exc
    return candidate


def _resolve_under_root(root: Path, path: Path, relative: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SnapshotError(f"unable to resolve {relative}: {exc}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SnapshotError(f"path escapes snapshot root: {relative}") from exc
    return resolved


def _list_directory(
    root: Path,
    path: Path,
    relative: str,
    expected: os.stat_result,
) -> list[Path]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SnapshotError(f"unable to open directory {relative}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise SnapshotError(f"snapshot entry is no longer a directory: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise SnapshotError(f"directory changed while opening: {relative}")
        try:
            names = (
                sorted(os.listdir(descriptor))
                if os.listdir in os.supports_fd
                else sorted(os.listdir(path))
            )
        except OSError as exc:
            raise SnapshotError(f"unable to list {relative}: {exc}") from exc
        after = os.fstat(descriptor)
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise SnapshotError(f"unable to restat directory {relative}: {exc}") from exc
        _resolve_under_root(root, path, relative)
        if _stat_identity(after) != _stat_identity(opened) or _stat_identity(
            current
        ) != _stat_identity(opened):
            raise SnapshotError(f"directory changed while listing: {relative}")
        return [path / name for name in names]
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _iter_selected(
    root: Path,
    includes: Iterable[str],
    excludes: tuple[str, ...],
    ignored_paths: set[Path],
) -> Iterable[tuple[str, Path]]:
    selected: dict[str, Path] = {}
    ignored_relatives = {
        path.relative_to(root).as_posix() for path in ignored_paths if path.is_relative_to(root)
    }
    for include in includes:
        candidate = _resolve_selected(root, include)
        if candidate == root:
            try:
                root_info = root.stat(follow_symlinks=False)
            except OSError as exc:
                raise SnapshotError(f"unable to stat snapshot root: {exc}") from exc
            if not stat.S_ISDIR(root_info.st_mode):
                raise SnapshotError("snapshot root changed during traversal")
            roots = _list_directory(root, root, ".", root_info)
        else:
            roots = [candidate]
        stack = list(reversed(roots))
        while stack:
            current = stack.pop()
            relative = _relative(root, current)
            if relative in ignored_relatives or _matches_exclude(relative, excludes):
                continue
            _resolve_under_root(root, current, relative)
            try:
                entry = current.stat(follow_symlinks=False)
            except OSError as exc:
                raise SnapshotError(f"unable to stat {relative}: {exc}") from exc
            if stat.S_ISLNK(entry.st_mode):
                raise SnapshotError(f"symbolic links are not supported: {relative}")
            if stat.S_ISDIR(entry.st_mode):
                selected[relative] = current
                children = _list_directory(root, current, relative, entry)
                stack.extend(reversed(children))
            elif stat.S_ISREG(entry.st_mode):
                selected[relative] = current
            else:
                raise SnapshotError(f"unsupported filesystem entry: {relative}")
    for relative in sorted(selected):
        yield relative, selected[relative]


def _read_file(
    root: Path,
    path: Path,
    relative: str,
    limits: SnapshotLimits,
    expected: os.stat_result,
) -> bytes:
    _resolve_under_root(root, path, relative)
    if expected.st_size > limits.max_file_bytes:
        raise SnapshotError(f"file exceeds max_file_bytes: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SnapshotError(f"unable to open {relative}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SnapshotError(f"snapshot entry is no longer a regular file: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise SnapshotError(f"file changed while opening: {relative}")
        data = bytearray()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                data.extend(chunk)
                if len(data) > limits.max_file_bytes:
                    raise SnapshotError(f"file exceeds max_file_bytes: {relative}")
        after = os.fstat(descriptor)
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise SnapshotError(f"unable to restat {relative}: {exc}") from exc
        _resolve_under_root(root, path, relative)
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or len(data) != opened.st_size
        ):
            raise SnapshotError(f"file changed while snapshotting: {relative}")
        return bytes(data)
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _zip_info(name: str, *, executable: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    mode = 0o100755 if executable else 0o100644
    info.external_attr = mode << 16
    return info


def create_snapshot(
    root: Path,
    output: Path,
    *,
    includes: Iterable[str] = (".",),
    excludes: Iterable[str] = DEFAULT_EXCLUDES,
    metadata: dict[str, Any] | None = None,
    limits: SnapshotLimits | None = None,
) -> SnapshotManifest:
    """Create a deterministic snapshot archive and return its manifest."""
    from e2h.snapshot_promotion import (
        _remove_regular_file_by_identity_at,
        promote_snapshot_file,
    )
    from e2h.snapshot_source import (
        _root_path_must_be_stable,
        collect_snapshot_source,
        resolve_snapshot_source_root,
    )

    limits = revalidate_snapshot_limits(limits)
    root, root_info = resolve_snapshot_source_root(root)
    output, parent_descriptor, parent_info = _open_snapshot_write_parent(
        output,
        noun="snapshot output",
    )
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    temporary_path: Path | None = None
    temporary_identity: tuple[int, int] | None = None
    output_identity: tuple[int, int] | None = None
    try:
        try:
            output_info = _stat_snapshot_write_entry(parent_descriptor, output)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SnapshotError(f"unable to inspect snapshot output: {exc}") from exc
        else:
            if stat.S_ISLNK(output_info.st_mode) or not stat.S_ISREG(output_info.st_mode):
                raise SnapshotError("snapshot output must be a regular file")
            output_identity = _inode_identity(output_info)
        ignored = {output}
        entries, blobs, total_bytes = collect_snapshot_source(
            root,
            root_info,
            includes=includes,
            excludes=excludes,
            ignored_paths=ignored,
            limits=limits,
        )
        core = SnapshotCore(entries=entries, total_bytes=total_bytes, metadata=metadata or {})
        manifest = SnapshotManifest(snapshot_id=_snapshot_id_validated(core), core=core)
        manifest_bytes = _canonical_json(manifest.model_dump(mode="json")) + b"\n"
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise SnapshotError("snapshot manifest is too large")
        _root_path_must_be_stable(root, root_info)
        _snapshot_write_parent_must_be_stable(
            output,
            parent_descriptor,
            parent_info,
            noun="snapshot output",
        )
        try:
            temporary_descriptor, temporary_name, temporary_path = _create_snapshot_temporary_file(
                parent_descriptor,
                output,
            )
            temporary_opened = os.fstat(temporary_descriptor)
            if not stat.S_ISREG(temporary_opened.st_mode):
                raise SnapshotError("snapshot temporary file is not a regular file")
            temporary_identity = _inode_identity(temporary_opened)
            with os.fdopen(temporary_descriptor, "w+b") as handle:
                temporary_descriptor = None
                with zipfile.ZipFile(handle, "w") as archive:
                    archive.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)
                    for digest in sorted(blobs):
                        archive.writestr(_zip_info(f"{BLOB_PREFIX}{digest}"), blobs[digest])
            _root_path_must_be_stable(root, root_info)
            _snapshot_write_parent_must_be_stable(
                output,
                parent_descriptor,
                parent_info,
                noun="snapshot output",
            )
            if _WRITE_DIR_FD_SUPPORTED:
                promote_snapshot_file(
                    output,
                    parent_descriptor,
                    parent_info,
                    temporary_name,
                    expected_identity=temporary_identity,
                )
            else:
                temporary_info = temporary_path.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(temporary_info.st_mode)
                    or _inode_identity(temporary_info) != temporary_identity
                ):
                    raise SnapshotError("snapshot temporary file changed before publication")
                backup = _backup_snapshot_output_path(output, output_identity)
                promoted = False
                success = False
                try:
                    os.replace(temporary_path, output)
                    promoted = True
                    current = output.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(current.st_mode)
                        or _inode_identity(current) != temporary_identity
                    ):
                        raise SnapshotError("snapshot output changed during publication")
                    _snapshot_write_parent_must_be_stable(
                        output,
                        parent_descriptor,
                        parent_info,
                        noun="snapshot output",
                    )
                    success = True
                finally:
                    if not success:
                        restored = (
                            _restore_snapshot_output_path(
                                output,
                                backup,
                                temporary_identity if promoted else None,
                            )
                            if backup is not None
                            else False
                        )
                        if backup is None and promoted:
                            _remove_regular_path_if_identity(Path(output), temporary_identity)
                        if restored:
                            backup = None
                    if success and backup is not None:
                        backup_path, backup_identity = backup
                        _remove_regular_path_if_identity(backup_path, backup_identity)
            temporary_name = None
            temporary_path = None
        except (OSError, zipfile.BadZipFile) as exc:
            raise SnapshotError(f"unable to write snapshot archive: {exc}") from exc
        return manifest
    finally:
        if temporary_descriptor is not None:
            with suppress(OSError):
                os.close(temporary_descriptor)
        if temporary_name is not None and temporary_identity is not None:
            if _CLEANUP_DIR_FD_SUPPORTED:
                _remove_regular_file_by_identity_at(parent_descriptor, temporary_identity)
        with suppress(OSError):
            os.close(parent_descriptor)


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SnapshotError(f"unsafe snapshot archive member: {name}")
    return name


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    if info.file_size > limit:
        raise SnapshotError(f"snapshot archive member is too large: {info.filename}")
    try:
        with archive.open(info, "r") as handle:
            data = handle.read(limit + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise SnapshotError(f"unable to read snapshot member {info.filename}: {exc}") from exc
    if len(data) > limit:
        raise SnapshotError(f"snapshot archive member is too large: {info.filename}")
    return data


def _verify_snapshot_archive(
    archive: zipfile.ZipFile,
    limits: SnapshotLimits,
) -> SnapshotManifest:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    for name in names:
        _safe_member_name(name)
    if len(names) != len(set(names)):
        raise SnapshotError("snapshot archive contains duplicate members")
    manifest_info = next((info for info in infos if info.filename == MANIFEST_NAME), None)
    if manifest_info is None:
        raise SnapshotError("snapshot archive is missing manifest.json")
    manifest_data = _read_member(archive, manifest_info, MAX_MANIFEST_BYTES)
    try:
        manifest_text = manifest_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SnapshotError("invalid snapshot manifest: manifest must be UTF-8") from exc
    try:
        raw_manifest = json.loads(
            manifest_text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
        manifest = SnapshotManifest.model_validate(raw_manifest)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SnapshotError(f"invalid snapshot manifest: {exc}") from exc
    if len(manifest.core.entries) > limits.max_entries:
        raise SnapshotError("snapshot exceeds max_entries")
    if manifest.core.total_bytes > limits.max_total_bytes:
        raise SnapshotError("snapshot exceeds max_total_bytes")
    expected_blobs = {
        entry.sha256: entry.size_bytes
        for entry in manifest.core.entries
        if entry.kind == "file" and entry.sha256 is not None
    }
    actual_blob_infos: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        if info.filename == MANIFEST_NAME:
            continue
        if not info.filename.startswith(BLOB_PREFIX):
            raise SnapshotError(f"unexpected snapshot archive member: {info.filename}")
        digest = info.filename.removeprefix(BLOB_PREFIX)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise SnapshotError(f"invalid snapshot blob name: {info.filename}")
        actual_blob_infos[digest] = info
    if set(actual_blob_infos) != set(expected_blobs):
        raise SnapshotError("snapshot blob set does not match the manifest")
    for digest, expected_size in expected_blobs.items():
        if expected_size > limits.max_file_bytes:
            raise SnapshotError(f"blob exceeds max_file_bytes: {digest}")
        data = _read_member(archive, actual_blob_infos[digest], limits.max_file_bytes)
        if len(data) != expected_size:
            raise SnapshotError(f"snapshot blob size mismatch: {digest}")
        if hashlib.sha256(data).hexdigest() != digest:
            raise SnapshotError(f"snapshot blob digest mismatch: {digest}")
    return manifest


def verify_snapshot(
    archive_path: Path,
    *,
    limits: SnapshotLimits | None = None,
) -> SnapshotManifest:
    """Verify archive structure, manifest identity, and every content blob."""
    limits = revalidate_snapshot_limits(limits)
    with _open_snapshot_archive_file(archive_path) as handle:
        try:
            archive = zipfile.ZipFile(handle, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise SnapshotError(f"unable to open snapshot archive: {exc}") from exc
        with archive:
            return _verify_snapshot_archive(archive, limits)


def restore_snapshot(
    archive_path: Path,
    destination: Path,
    *,
    limits: SnapshotLimits | None = None,
) -> SnapshotManifest:
    """Verify and atomically restore a snapshot into a new or empty directory."""
    from e2h.snapshot_promotion import promote_restore_tree

    limits = revalidate_snapshot_limits(limits)
    destination, parent_descriptor, parent_info = _open_snapshot_write_parent(
        destination,
        noun="restore destination",
    )
    staging_name: str | None = None
    staging_path: Path | None = None
    staging_descriptor: int | None = None
    staging_identity: tuple[int, int] | None = None
    try:
        _restore_destination_is_empty(parent_descriptor, destination)
        _snapshot_write_parent_must_be_stable(
            destination,
            parent_descriptor,
            parent_info,
            noun="restore destination",
        )
        staging_name, staging_path = _create_restore_staging_directory(
            parent_descriptor,
            destination,
        )
        staging_expected = _stat_snapshot_write_entry(parent_descriptor, staging_path)
        if stat.S_ISLNK(staging_expected.st_mode) or not stat.S_ISDIR(staging_expected.st_mode):
            raise SnapshotError("restore staging entry is not a directory")
        staging_identity = _inode_identity(staging_expected)
        if _WRITE_DIR_FD_SUPPORTED:
            staging_descriptor = _open_bound_child_directory(
                parent_descriptor,
                staging_name,
                staging_expected,
                noun="restore staging directory",
            )
        try:
            with _open_snapshot_archive_file(archive_path) as handle:
                try:
                    archive = zipfile.ZipFile(handle, "r")
                except (OSError, zipfile.BadZipFile) as exc:
                    raise SnapshotError(f"unable to open snapshot archive: {exc}") from exc
                with archive:
                    manifest = _verify_snapshot_archive(archive, limits)
                    for entry in manifest.core.entries:
                        parts = tuple(PurePosixPath(entry.path).parts)
                        if _WRITE_DIR_FD_SUPPORTED:
                            assert staging_descriptor is not None
                            if entry.kind == "directory":
                                directory_descriptor = _open_restore_directory(
                                    staging_descriptor,
                                    parts,
                                )
                                with suppress(OSError):
                                    os.close(directory_descriptor)
                                continue
                            assert entry.sha256 is not None
                            info = archive.getinfo(f"{BLOB_PREFIX}{entry.sha256}")
                            data = _read_member(archive, info, limits.max_file_bytes)
                            _write_restore_file_at(
                                staging_descriptor,
                                parts,
                                data,
                                executable=entry.executable,
                            )
                            continue
                        assert staging_path is not None
                        target = (staging_path / Path(*parts)).resolve()
                        try:
                            target.relative_to(staging_path)
                        except ValueError as exc:
                            raise SnapshotError(
                                f"restore path escapes staging root: {entry.path}"
                            ) from exc
                        if entry.kind == "directory":
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        assert entry.sha256 is not None
                        target.parent.mkdir(parents=True, exist_ok=True)
                        info = archive.getinfo(f"{BLOB_PREFIX}{entry.sha256}")
                        data = _read_member(archive, info, limits.max_file_bytes)
                        target.write_bytes(data)
                        target.chmod(0o755 if entry.executable else 0o644)
        finally:
            if staging_descriptor is not None:
                with suppress(OSError):
                    os.close(staging_descriptor)
                staging_descriptor = None
        _snapshot_write_parent_must_be_stable(
            destination,
            parent_descriptor,
            parent_info,
            noun="restore destination",
        )
        destination_exists = _restore_destination_is_empty(parent_descriptor, destination)
        _snapshot_write_parent_must_be_stable(
            destination,
            parent_descriptor,
            parent_info,
            noun="restore destination",
        )
        assert staging_identity is not None
        if _WRITE_DIR_FD_SUPPORTED:
            promote_restore_tree(
                destination,
                parent_descriptor,
                parent_info,
                staging_name,
                expected_identity=staging_identity,
            )
        else:
            staging_info = staging_path.stat(follow_symlinks=False)
            if (
                stat.S_ISLNK(staging_info.st_mode)
                or not stat.S_ISDIR(staging_info.st_mode)
                or _inode_identity(staging_info) != staging_identity
            ):
                raise SnapshotError("restore staging directory changed before publication")
            if destination_exists:
                destination.rmdir()
            os.replace(staging_path, destination)
            current = destination.stat(follow_symlinks=False)
            if (
                stat.S_ISLNK(current.st_mode)
                or not stat.S_ISDIR(current.st_mode)
                or _inode_identity(current) != staging_identity
            ):
                raise SnapshotError("restore destination changed during publication")
            _snapshot_write_parent_must_be_stable(
                destination,
                parent_descriptor,
                parent_info,
                noun="restore destination",
            )
        staging_name = None
        staging_path = None
    except SnapshotError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise SnapshotError(f"unable to restore snapshot: {exc}") from exc
    finally:
        if staging_descriptor is not None:
            with suppress(OSError):
                os.close(staging_descriptor)
        if staging_name is not None and staging_path is not None:
            _remove_restore_tree_at(
                parent_descriptor,
                staging_name,
                staging_path,
                expected_identity=staging_identity,
            )
        with suppress(OSError):
            os.close(parent_descriptor)
    return manifest


def snapshot_reference(
    archive_path: Path,
    *,
    locator: str | None = None,
    role: Literal["workspace", "artifact"] = "workspace",
) -> SnapshotReference:
    """Verify an archive and return a portable content-addressed reference."""
    limits = revalidate_snapshot_limits(None)
    with _open_snapshot_archive_file(archive_path) as handle:
        try:
            archive = zipfile.ZipFile(handle, "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise SnapshotError(f"unable to open snapshot archive: {exc}") from exc
        with archive:
            manifest = _verify_snapshot_archive(archive, limits)
        archive_digest = _archive_sha256_handle(handle)
    return SnapshotReference(
        snapshot_id=manifest.snapshot_id,
        archive_sha256=archive_digest,
        locator=locator or archive_path.name,
        role=role,
    )
