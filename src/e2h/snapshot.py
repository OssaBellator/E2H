"""Deterministic content-addressed workspace snapshot bundles."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
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


class SnapshotError(ValueError):
    """Raised when snapshot creation, verification, or restoration fails."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _canonical_json(value: Any) -> bytes:
    try:
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
        if self.snapshot_id != snapshot_id(self.core):
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


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def snapshot_id(core: SnapshotCore) -> str:
    payload = _canonical_json(core.model_dump(mode="json"))
    return hashlib.sha256(payload).hexdigest()


@contextmanager
def _open_snapshot_archive_file(path: Path) -> Iterator[BinaryIO]:
    try:
        parent = path.parent.resolve(strict=True)
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
    limits = revalidate_snapshot_limits(limits)
    root = root.resolve()
    if not root.is_dir():
        raise SnapshotError(f"snapshot root is not a directory: {root}")
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    ignored = {output}
    entries: list[SnapshotEntry] = []
    blobs: dict[str, bytes] = {}
    total_bytes = 0
    for relative, path in _iter_selected(root, includes, tuple(excludes), ignored):
        if len(entries) >= limits.max_entries:
            raise SnapshotError("snapshot exceeds max_entries")
        _resolve_under_root(root, path, relative)
        try:
            entry = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise SnapshotError(f"unable to stat {relative}: {exc}") from exc
        if stat.S_ISLNK(entry.st_mode):
            raise SnapshotError(f"symbolic links are not supported: {relative}")
        if stat.S_ISDIR(entry.st_mode):
            entries.append(SnapshotEntry(path=relative, kind="directory"))
            continue
        if not stat.S_ISREG(entry.st_mode):
            raise SnapshotError(f"unsupported filesystem entry: {relative}")
        data = _read_file(root, path, relative, limits, entry)
        total_bytes += len(data)
        if total_bytes > limits.max_total_bytes:
            raise SnapshotError("snapshot exceeds max_total_bytes")
        digest = hashlib.sha256(data).hexdigest()
        executable = bool(entry.st_mode & stat.S_IXUSR)
        entries.append(
            SnapshotEntry(
                path=relative,
                kind="file",
                sha256=digest,
                size_bytes=len(data),
                executable=executable,
            )
        )
        blobs.setdefault(digest, data)
    core = SnapshotCore(entries=entries, total_bytes=total_bytes, metadata=metadata or {})
    manifest = SnapshotManifest(snapshot_id=snapshot_id(core), core=core)
    manifest_bytes = _canonical_json(manifest.model_dump(mode="json")) + b"\n"
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise SnapshotError("snapshot manifest is too large")
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            archive.writestr(_zip_info(MANIFEST_NAME), manifest_bytes)
            for digest in sorted(blobs):
                archive.writestr(_zip_info(f"{BLOB_PREFIX}{digest}"), blobs[digest])
        os.replace(temporary, output)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SnapshotError(f"unable to write snapshot archive: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


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
        raw_manifest = json.loads(manifest_data)
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
    limits = revalidate_snapshot_limits(limits)
    destination = destination.resolve()
    if destination.exists() and not destination.is_dir():
        raise SnapshotError("restore destination exists and is not a directory")
    if destination.exists():
        try:
            if any(destination.iterdir()):
                raise SnapshotError("restore destination must be empty")
        except OSError as exc:
            raise SnapshotError(f"unable to inspect restore destination: {exc}") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.restore-", dir=destination.parent)
    ).resolve()
    try:
        with _open_snapshot_archive_file(archive_path) as handle:
            try:
                archive = zipfile.ZipFile(handle, "r")
            except (OSError, zipfile.BadZipFile) as exc:
                raise SnapshotError(f"unable to open snapshot archive: {exc}") from exc
            with archive:
                manifest = _verify_snapshot_archive(archive, limits)
                for entry in manifest.core.entries:
                    target = (staging / Path(*PurePosixPath(entry.path).parts)).resolve()
                    try:
                        target.relative_to(staging)
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
        if destination.exists():
            destination.rmdir()
        os.replace(staging, destination)
    except SnapshotError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise SnapshotError(f"unable to restore snapshot: {exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
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
