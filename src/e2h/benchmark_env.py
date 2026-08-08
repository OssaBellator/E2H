"""Content-addressed reproducible environments for E2H community benchmarks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import load_mapping_document

_MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
_MAX_ENVIRONMENT_BYTES = 64 * 1024 * 1024
_MAX_FILES = 10_000
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_LISTDIR_SUPPORTS_FD = os.listdir in os.supports_fd
_MKDIR_SUPPORTS_DIR_FD = os.mkdir in os.supports_dir_fd
_RMDIR_SUPPORTS_DIR_FD = os.rmdir in os.supports_dir_fd
_UNLINK_SUPPORTS_DIR_FD = os.unlink in os.supports_dir_fd
_UTIME_SUPPORTS_FD = os.utime in os.supports_fd
_MATERIALIZATION_DIR_FD_SUPPORTED = (
    _OPEN_SUPPORTS_DIR_FD
    and _STAT_SUPPORTS_DIR_FD
    and _LISTDIR_SUPPORTS_FD
    and _MKDIR_SUPPORTS_DIR_FD
    and _RMDIR_SUPPORTS_DIR_FD
    and _UNLINK_SUPPORTS_DIR_FD
    and _UTIME_SUPPORTS_FD
    and hasattr(os, "fchmod")
)


class BenchmarkEnvironmentError(ValueError):
    """Raised when a benchmark environment spec, lock, or filesystem tree is unsafe."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_InputModelT = TypeVar("_InputModelT", bound=BaseModel)


def _revalidate_benchmark_environment_model(
    value: BaseModel,
    model_type: type[_InputModelT],
    *,
    noun: str,
) -> _InputModelT:
    """Return a detached benchmark-environment model after enforcing its concrete type."""
    if type(value) is not model_type:
        raise BenchmarkEnvironmentError(
            f"invalid {noun}: expected {model_type.__name__}, got {type(value).__name__}"
        )
    try:
        payload = value.model_dump(mode="python", warnings="none")
        return model_type.model_validate(payload)
    except ValueError as exc:
        raise BenchmarkEnvironmentError(f"invalid {noun}: {exc}") from exc


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must contain canonical JSON data") from exc
    return rendered.encode("utf-8")


def _validate_relative_path(value: str, noun: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ValueError(f"{noun} must be a non-empty portable relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/"):
        raise ValueError(f"{noun} must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{noun} must not contain empty, dot, or parent segments")
    return value


def _safe_root(root: Path) -> Path:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise BenchmarkEnvironmentError("benchmark environment root is not a directory")
    return resolved


def _resolve_relative(root: Path, relative: str, noun: str) -> Path:
    try:
        normalized = _validate_relative_path(relative, noun)
    except ValueError as exc:
        raise BenchmarkEnvironmentError(str(exc)) from exc
    candidate = (root / Path(*PurePosixPath(normalized).parts)).resolve()
    if not candidate.is_relative_to(root):
        raise BenchmarkEnvironmentError(f"{noun} escapes the benchmark root")
    return candidate


def _read_mapping(path: Path, *, noun: str) -> dict[str, Any]:
    try:
        return load_mapping_document(path, noun=noun, max_bytes=_MAX_DOCUMENT_BYTES)
    except ValueError as exc:
        raise BenchmarkEnvironmentError(str(exc)) from exc


class BenchmarkEnvironmentKind(StrEnum):
    CODING = "coding"
    RESEARCH = "research"
    BROWSER = "browser"


class BenchmarkNetworkPolicy(StrEnum):
    NONE = "none"
    LOCALHOST_ONLY = "localhost_only"


class BenchmarkEnvironmentSpec(StrictModel):
    """Human-authored contract for one reproducible benchmark environment."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    kind: BenchmarkEnvironmentKind
    source_dir: str = Field(min_length=1, max_length=4096)
    network: BenchmarkNetworkPolicy
    entrypoint: list[str] = Field(min_length=1, max_length=64)
    candidate_artifact: str = Field(min_length=1, max_length=4096)
    description: str = Field(min_length=1, max_length=2_000)

    @field_validator("source_dir")
    @classmethod
    def source_dir_must_be_relative(cls, value: str) -> str:
        return _validate_relative_path(value, "environment source_dir")

    @field_validator("candidate_artifact")
    @classmethod
    def candidate_artifact_must_be_relative(cls, value: str) -> str:
        return _validate_relative_path(value, "candidate_artifact")

    @field_validator("entrypoint")
    @classmethod
    def entrypoint_must_be_safe(cls, values: list[str]) -> list[str]:
        if any(not value or "\x00" in value or len(value) > 4096 for value in values):
            raise ValueError("entrypoint arguments must contain 1 to 4096 safe characters")
        return values

    @model_validator(mode="after")
    def network_must_match_environment_kind(self) -> BenchmarkEnvironmentSpec:
        if self.kind is BenchmarkEnvironmentKind.BROWSER:
            if self.network is not BenchmarkNetworkPolicy.LOCALHOST_ONLY:
                raise ValueError("browser environments must use localhost_only network policy")
        elif self.network is not BenchmarkNetworkPolicy.NONE:
            raise ValueError("coding and research environments must use network policy none")
        return self


class BenchmarkEnvironmentSuite(StrictModel):
    """Human-authored suite whose source trees are sealed by a generated lock document."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=255)
    environments: list[BenchmarkEnvironmentSpec] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def environment_ids_and_kinds_must_be_unique(self) -> BenchmarkEnvironmentSuite:
        ids = [environment.id for environment in self.environments]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark environment ids must be unique")
        kinds = [environment.kind for environment in self.environments]
        if len(kinds) != len(set(kinds)):
            raise ValueError("benchmark environment kinds must be unique within a suite")
        return self


class BenchmarkEnvironmentFile(StrictModel):
    """One regular file in a sealed environment tree."""

    path: str = Field(min_length=1, max_length=4096)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    size: int = Field(ge=0)
    executable: bool

    @field_validator("path")
    @classmethod
    def path_must_be_relative(cls, value: str) -> str:
        return _validate_relative_path(value, "environment file path")


class BenchmarkEnvironmentLockEntry(StrictModel):
    """Content-addressed identity of one benchmark environment tree."""

    id: str = Field(pattern=_ID_PATTERN)
    kind: BenchmarkEnvironmentKind
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    file_count: int = Field(ge=1, le=_MAX_FILES)
    total_bytes: int = Field(ge=0, le=_MAX_ENVIRONMENT_BYTES)
    files: list[BenchmarkEnvironmentFile] = Field(min_length=1, max_length=_MAX_FILES)

    @model_validator(mode="after")
    def file_summary_must_match(self) -> BenchmarkEnvironmentLockEntry:
        paths = [item.path for item in self.files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("locked environment files must have unique sorted paths")
        if self.file_count != len(self.files):
            raise ValueError("locked environment file_count does not match files")
        if self.total_bytes != sum(item.size for item in self.files):
            raise ValueError("locked environment total_bytes does not match files")
        expected = _tree_digest(self.files)
        if self.source_sha256 != expected:
            raise ValueError("locked environment source_sha256 does not match files")
        return self


class BenchmarkEnvironmentSuiteLock(StrictModel):
    """Generated lock that binds a suite spec to all environment source bytes."""

    schema_version: Literal["0.1"] = "0.1"
    suite_id: str = Field(pattern=_ID_PATTERN)
    suite_sha256: str = Field(pattern=_SHA256_PATTERN)
    environments: list[BenchmarkEnvironmentLockEntry] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def environment_ids_must_be_unique(self) -> BenchmarkEnvironmentSuiteLock:
        ids = [environment.id for environment in self.environments]
        if len(ids) != len(set(ids)):
            raise ValueError("locked benchmark environment ids must be unique")
        return self


class BenchmarkEnvironmentVerification(StrictModel):
    """Verification summary for a suite and its generated lock."""

    schema_version: Literal["0.1"] = "0.1"
    suite_id: str = Field(pattern=_ID_PATTERN)
    suite_sha256: str = Field(pattern=_SHA256_PATTERN)
    environment_count: int = Field(ge=1)
    file_count: int = Field(ge=1)
    total_bytes: int = Field(ge=0)
    environment_sha256: dict[str, str]
    verified: bool


@dataclass(frozen=True)
class _ScannedEnvironment:
    files: list[BenchmarkEnvironmentFile]
    source_sha256: str
    total_bytes: int


def _benchmark_environment_suite_sha256_validated(suite: BenchmarkEnvironmentSuite) -> str:
    return hashlib.sha256(_canonical_json_bytes(suite.model_dump(mode="json"))).hexdigest()


def benchmark_environment_suite_sha256(suite: BenchmarkEnvironmentSuite) -> str:
    """Return the canonical identity of a human-authored environment suite."""
    suite = _validated_benchmark_environment_suite(suite)
    return _benchmark_environment_suite_sha256_validated(suite)


def _tree_digest(files: list[BenchmarkEnvironmentFile]) -> str:
    material = [item.model_dump(mode="json") for item in files]
    return hashlib.sha256(_canonical_json_bytes(material)).hexdigest()


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    return (info.st_dev, info.st_ino, info.st_mode)


def _open_materialization_parent(destination: Path) -> tuple[Path, int, os.stat_result]:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        parent = destination.parent.resolve(strict=True)
        expected = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to prepare environment materialization destination: {exc}"
        ) from exc
    if not stat.S_ISDIR(expected.st_mode):
        raise BenchmarkEnvironmentError(
            "environment materialization destination parent is not a directory"
        )
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(parent, flags)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to open environment materialization destination parent: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _stat_identity(opened) != _stat_identity(expected):
            raise BenchmarkEnvironmentError(
                "environment materialization destination parent changed while opening"
            )
        return parent / destination.name, descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _materialization_parent_must_be_stable(
    destination: Path,
    descriptor: int,
    opened: os.stat_result,
) -> None:
    try:
        after = os.fstat(descriptor)
        current = destination.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to restat environment materialization destination parent: {exc}"
        ) from exc
    if _directory_identity(after) != _directory_identity(opened) or _directory_identity(
        current
    ) != _directory_identity(opened):
        raise BenchmarkEnvironmentError(
            "environment materialization destination parent changed while writing"
        )


def _stat_materialization_entry(parent_descriptor: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)


def _open_bound_materialized_directory(
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
        raise BenchmarkEnvironmentError(f"unable to open {noun}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _stat_identity(opened) != _stat_identity(expected):
            raise BenchmarkEnvironmentError(f"{noun} changed while opening")
        return descriptor
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _open_materialized_directory(
    root_descriptor: int,
    parts: tuple[str, ...],
    *,
    create: bool,
) -> int:
    current = os.dup(root_descriptor)
    try:
        for part in parts:
            try:
                expected = _stat_materialization_entry(current, part)
            except FileNotFoundError:
                if not create:
                    raise BenchmarkEnvironmentError(
                        f"materialized environment directory is missing: {part}"
                    ) from None
                os.mkdir(part, 0o700, dir_fd=current)
                expected = _stat_materialization_entry(current, part)
            except OSError as exc:
                raise BenchmarkEnvironmentError(
                    f"unable to inspect materialized environment directory {part}: {exc}"
                ) from exc
            if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
                raise BenchmarkEnvironmentError(
                    f"materialized environment path component is not a directory: {part}"
                )
            child = _open_bound_materialized_directory(
                current,
                part,
                expected,
                noun=f"materialized environment directory {part}",
            )
            os.close(current)
            current = child
        return current
    except Exception:
        with suppress(OSError):
            os.close(current)
        raise


def _remove_materialized_tree_at(parent_descriptor: int, name: str) -> None:
    try:
        expected = _stat_materialization_entry(parent_descriptor, name)
    except OSError:
        return
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
        with suppress(OSError):
            os.unlink(name, dir_fd=parent_descriptor)
        return
    descriptor: int | None = None
    try:
        descriptor = _open_bound_materialized_directory(
            parent_descriptor,
            name,
            expected,
            noun="materialized environment destination",
        )
        for child_name in os.listdir(descriptor):
            try:
                child = _stat_materialization_entry(descriptor, child_name)
            except OSError:
                continue
            if stat.S_ISDIR(child.st_mode) and not stat.S_ISLNK(child.st_mode):
                _remove_materialized_tree_at(descriptor, child_name)
            else:
                with suppress(OSError):
                    os.unlink(child_name, dir_fd=descriptor)
    except BenchmarkEnvironmentError:
        return
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    with suppress(OSError):
        os.rmdir(name, dir_fd=parent_descriptor)


def _resolve_under_source(source: Path, path: Path, relative: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to resolve environment entry {relative}: {exc}"
        ) from exc
    try:
        resolved.relative_to(source)
    except ValueError as exc:
        raise BenchmarkEnvironmentError(
            f"environment entry escapes source_dir: {relative}"
        ) from exc
    return resolved


def _list_environment_directory(
    source: Path,
    path: Path,
    relative: str,
    expected: os.stat_result,
) -> list[Path]:
    _resolve_under_source(source, path, relative)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to open environment directory {relative}: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise BenchmarkEnvironmentError(
                f"environment entry is no longer a directory: {relative}"
            )
        if _stat_identity(opened) != _stat_identity(expected):
            raise BenchmarkEnvironmentError(
                f"environment directory changed while opening: {relative}"
            )
        try:
            names = (
                sorted(os.listdir(descriptor))
                if os.listdir in os.supports_fd
                else sorted(os.listdir(path))
            )
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to list environment directory {relative}: {exc}"
            ) from exc
        after = os.fstat(descriptor)
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to restat environment directory {relative}: {exc}"
            ) from exc
        _resolve_under_source(source, path, relative)
        if _stat_identity(after) != _stat_identity(opened) or _stat_identity(
            current
        ) != _stat_identity(opened):
            raise BenchmarkEnvironmentError(
                f"environment directory changed while listing: {relative}"
            )
        return [path / name for name in names]
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _hash_environment_file(
    source: Path,
    path: Path,
    relative: str,
    expected: os.stat_result,
) -> tuple[str, int, bool]:
    _resolve_under_source(source, path, relative)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to open environment file {relative}: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise BenchmarkEnvironmentError(f"environment contains non-regular file: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise BenchmarkEnvironmentError(f"environment file changed while opening: {relative}")
        if opened.st_size > _MAX_ENVIRONMENT_BYTES:
            raise BenchmarkEnvironmentError(
                f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
            )
        digest = hashlib.sha256()
        observed = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                observed += len(chunk)
                if observed > _MAX_ENVIRONMENT_BYTES:
                    raise BenchmarkEnvironmentError(
                        f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
                    )
                digest.update(chunk)
        after = os.fstat(descriptor)
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to restat environment file {relative}: {exc}"
            ) from exc
        _resolve_under_source(source, path, relative)
        if (
            _stat_identity(after) != _stat_identity(opened)
            or observed != opened.st_size
            or _stat_identity(current) != _stat_identity(opened)
        ):
            raise BenchmarkEnvironmentError(f"environment file changed while hashing: {relative}")
        return digest.hexdigest(), observed, bool(opened.st_mode & stat.S_IXUSR)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to hash environment file {relative}: {exc}"
        ) from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _scan_environment(source: Path) -> _ScannedEnvironment:
    try:
        source_info = source.stat(follow_symlinks=False)
    except OSError as exc:
        raise BenchmarkEnvironmentError(f"unable to stat environment source_dir: {exc}") from exc
    if stat.S_ISLNK(source_info.st_mode) or not stat.S_ISDIR(source_info.st_mode):
        raise BenchmarkEnvironmentError("environment source_dir is not a directory")
    roots = _list_environment_directory(source, source, ".", source_info)
    files: list[BenchmarkEnvironmentFile] = []
    total_bytes = 0
    stack = list(reversed(roots))
    while stack:
        path = stack.pop()
        relative = path.relative_to(source).as_posix()
        _resolve_under_source(source, path, relative)
        try:
            entry = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to stat environment entry {relative}: {exc}"
            ) from exc
        if stat.S_ISLNK(entry.st_mode):
            raise BenchmarkEnvironmentError(f"environment contains symlink: {relative}")
        if stat.S_ISDIR(entry.st_mode):
            children = _list_environment_directory(source, path, relative, entry)
            stack.extend(reversed(children))
            continue
        if not stat.S_ISREG(entry.st_mode):
            raise BenchmarkEnvironmentError(f"environment contains non-regular file: {relative}")
        if len(files) >= _MAX_FILES:
            raise BenchmarkEnvironmentError(f"environment exceeds {_MAX_FILES} files")
        if total_bytes + entry.st_size > _MAX_ENVIRONMENT_BYTES:
            raise BenchmarkEnvironmentError(
                f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
            )
        digest, size, executable = _hash_environment_file(source, path, relative, entry)
        total_bytes += size
        files.append(
            BenchmarkEnvironmentFile(
                path=relative,
                sha256=digest,
                size=size,
                executable=executable,
            )
        )
    if not files:
        raise BenchmarkEnvironmentError("environment source_dir contains no files")
    return _ScannedEnvironment(
        files=files,
        source_sha256=_tree_digest(files),
        total_bytes=total_bytes,
    )


def _hash_materialized_file(
    directory_descriptor: int,
    name: str,
    relative: str,
    expected: os.stat_result,
) -> tuple[str, int, bool]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to open materialized environment file {relative}: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _stat_identity(opened) != _stat_identity(expected):
            raise BenchmarkEnvironmentError(
                f"materialized environment file changed while opening: {relative}"
            )
        digest = hashlib.sha256()
        observed = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                observed += len(chunk)
                if observed > _MAX_ENVIRONMENT_BYTES:
                    raise BenchmarkEnvironmentError(
                        f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
                    )
                digest.update(chunk)
        after = os.fstat(descriptor)
        current = _stat_materialization_entry(directory_descriptor, name)
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or observed != opened.st_size
        ):
            raise BenchmarkEnvironmentError(
                f"materialized environment file changed while hashing: {relative}"
            )
        return digest.hexdigest(), observed, bool(opened.st_mode & stat.S_IXUSR)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to hash materialized environment file {relative}: {exc}"
        ) from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _scan_materialized_environment(root_descriptor: int) -> _ScannedEnvironment:
    files: list[BenchmarkEnvironmentFile] = []
    total_bytes = 0
    stack: list[tuple[str, ...]] = [()]
    while stack:
        parts = stack.pop()
        directory_descriptor = _open_materialized_directory(
            root_descriptor,
            parts,
            create=False,
        )
        try:
            try:
                names = sorted(os.listdir(directory_descriptor))
            except OSError as exc:
                relative = PurePosixPath(*parts).as_posix() if parts else "."
                raise BenchmarkEnvironmentError(
                    f"unable to list materialized environment directory {relative}: {exc}"
                ) from exc
            for name in names:
                relative_parts = (*parts, name)
                relative = PurePosixPath(*relative_parts).as_posix()
                try:
                    entry = _stat_materialization_entry(directory_descriptor, name)
                except OSError as exc:
                    raise BenchmarkEnvironmentError(
                        f"unable to stat materialized environment entry {relative}: {exc}"
                    ) from exc
                if stat.S_ISLNK(entry.st_mode):
                    raise BenchmarkEnvironmentError(
                        f"materialized environment contains symlink: {relative}"
                    )
                if stat.S_ISDIR(entry.st_mode):
                    stack.append(relative_parts)
                    continue
                if not stat.S_ISREG(entry.st_mode):
                    raise BenchmarkEnvironmentError(
                        f"materialized environment contains non-regular file: {relative}"
                    )
                if len(files) >= _MAX_FILES:
                    raise BenchmarkEnvironmentError(f"environment exceeds {_MAX_FILES} files")
                if total_bytes + entry.st_size > _MAX_ENVIRONMENT_BYTES:
                    raise BenchmarkEnvironmentError(
                        f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
                    )
                digest, size, executable = _hash_materialized_file(
                    directory_descriptor,
                    name,
                    relative,
                    entry,
                )
                total_bytes += size
                files.append(
                    BenchmarkEnvironmentFile(
                        path=relative,
                        sha256=digest,
                        size=size,
                        executable=executable,
                    )
                )
        finally:
            with suppress(OSError):
                os.close(directory_descriptor)
    if not files:
        raise BenchmarkEnvironmentError("environment source_dir contains no files")
    files.sort(key=lambda item: item.path)
    return _ScannedEnvironment(
        files=files,
        source_sha256=_tree_digest(files),
        total_bytes=total_bytes,
    )


def _copy_environment_file(
    source: Path,
    path: Path,
    relative: str,
    expected: os.stat_result,
    destination: Path,
) -> int:
    _resolve_under_source(source, path, relative)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to open environment file {relative} for materialization: {exc}"
        ) from exc
    destination_descriptor: int | None = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise BenchmarkEnvironmentError(f"environment contains non-regular file: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise BenchmarkEnvironmentError(f"environment file changed while opening: {relative}")
        if opened.st_size > _MAX_ENVIRONMENT_BYTES:
            raise BenchmarkEnvironmentError(
                f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
            )
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            destination_descriptor = os.open(destination, destination_flags, 0o600)
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to create materialized environment file {relative}: {exc}"
            ) from exc
        observed = 0
        try:
            with (
                os.fdopen(descriptor, "rb", closefd=False) as source_handle,
                os.fdopen(destination_descriptor, "wb", closefd=False) as destination_handle,
            ):
                while chunk := source_handle.read(1024 * 1024):
                    observed += len(chunk)
                    if observed > _MAX_ENVIRONMENT_BYTES:
                        raise BenchmarkEnvironmentError(
                            f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
                        )
                    destination_handle.write(chunk)
            os.fchmod(destination_descriptor, stat.S_IMODE(opened.st_mode))
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to copy environment file {relative}: {exc}"
            ) from exc
        after = os.fstat(descriptor)
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to restat environment file {relative}: {exc}"
            ) from exc
        _resolve_under_source(source, path, relative)
        if (
            _stat_identity(after) != _stat_identity(opened)
            or observed != opened.st_size
            or _stat_identity(current) != _stat_identity(opened)
        ):
            raise BenchmarkEnvironmentError(
                f"environment file changed while materializing: {relative}"
            )
        try:
            os.utime(
                destination,
                ns=(opened.st_atime_ns, opened.st_mtime_ns),
                follow_symlinks=False,
            )
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to preserve environment file metadata {relative}: {exc}"
            ) from exc
        return observed
    finally:
        if destination_descriptor is not None:
            with suppress(OSError):
                os.close(destination_descriptor)
        with suppress(OSError):
            os.close(descriptor)


def _copy_environment_file_at(
    source: Path,
    path: Path,
    relative: str,
    expected: os.stat_result,
    root_descriptor: int,
    parts: tuple[str, ...],
) -> int:
    _resolve_under_source(source, path, relative)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to open environment file {relative} for materialization: {exc}"
        ) from exc
    parent_descriptor = _open_materialized_directory(
        root_descriptor,
        parts[:-1],
        create=False,
    )
    destination_descriptor: int | None = None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise BenchmarkEnvironmentError(f"environment contains non-regular file: {relative}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise BenchmarkEnvironmentError(f"environment file changed while opening: {relative}")
        if opened.st_size > _MAX_ENVIRONMENT_BYTES:
            raise BenchmarkEnvironmentError(
                f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
            )
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            destination_descriptor = os.open(
                parts[-1],
                destination_flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to create materialized environment file {relative}: {exc}"
            ) from exc
        observed = 0
        try:
            with (
                os.fdopen(descriptor, "rb", closefd=False) as source_handle,
                os.fdopen(destination_descriptor, "wb", closefd=False) as destination_handle,
            ):
                while chunk := source_handle.read(1024 * 1024):
                    observed += len(chunk)
                    if observed > _MAX_ENVIRONMENT_BYTES:
                        raise BenchmarkEnvironmentError(
                            f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
                        )
                    destination_handle.write(chunk)
            os.fchmod(destination_descriptor, stat.S_IMODE(opened.st_mode))
            os.utime(
                destination_descriptor,
                ns=(opened.st_atime_ns, opened.st_mtime_ns),
            )
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to copy environment file {relative}: {exc}"
            ) from exc
        after = os.fstat(descriptor)
        try:
            current = path.stat(follow_symlinks=False)
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to restat environment file {relative}: {exc}"
            ) from exc
        _resolve_under_source(source, path, relative)
        if (
            _stat_identity(after) != _stat_identity(opened)
            or observed != opened.st_size
            or _stat_identity(current) != _stat_identity(opened)
        ):
            raise BenchmarkEnvironmentError(
                f"environment file changed while materializing: {relative}"
            )
        return observed
    finally:
        if destination_descriptor is not None:
            with suppress(OSError):
                os.close(destination_descriptor)
        with suppress(OSError):
            os.close(parent_descriptor)
        with suppress(OSError):
            os.close(descriptor)


def _copy_environment_tree_path(
    source: Path,
    destination: Path,
    *,
    expected: BenchmarkEnvironmentLockEntry | None,
) -> _ScannedEnvironment:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = destination.parent.resolve(strict=True) / destination.name
        try:
            destination.stat(follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise BenchmarkEnvironmentError(
                "environment materialization destination already exists"
            )
    except BenchmarkEnvironmentError:
        raise
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to prepare environment materialization destination: {exc}"
        ) from exc
    try:
        source_info = source.stat(follow_symlinks=False)
    except OSError as exc:
        raise BenchmarkEnvironmentError(f"unable to stat environment source_dir: {exc}") from exc
    if stat.S_ISLNK(source_info.st_mode) or not stat.S_ISDIR(source_info.st_mode):
        raise BenchmarkEnvironmentError("environment source_dir is not a directory")
    try:
        destination.mkdir(parents=True)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to create benchmark environment destination: {exc}"
        ) from exc
    try:
        roots = _list_environment_directory(source, source, ".", source_info)
        directory_metadata: list[tuple[Path, os.stat_result]] = [(destination, source_info)]
        total_bytes = 0
        file_count = 0
        stack = list(reversed(roots))
        while stack:
            path = stack.pop()
            relative = path.relative_to(source).as_posix()
            _resolve_under_source(source, path, relative)
            try:
                entry = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise BenchmarkEnvironmentError(
                    f"unable to stat environment entry {relative}: {exc}"
                ) from exc
            if stat.S_ISLNK(entry.st_mode):
                raise BenchmarkEnvironmentError(f"environment contains symlink: {relative}")
            destination_path = destination / Path(*PurePosixPath(relative).parts)
            if stat.S_ISDIR(entry.st_mode):
                children = _list_environment_directory(source, path, relative, entry)
                try:
                    destination_path.mkdir()
                except OSError as exc:
                    raise BenchmarkEnvironmentError(
                        f"unable to create materialized environment directory {relative}: {exc}"
                    ) from exc
                directory_metadata.append((destination_path, entry))
                stack.extend(reversed(children))
                continue
            if not stat.S_ISREG(entry.st_mode):
                raise BenchmarkEnvironmentError(
                    f"environment contains non-regular file: {relative}"
                )
            if file_count >= _MAX_FILES:
                raise BenchmarkEnvironmentError(f"environment exceeds {_MAX_FILES} files")
            if total_bytes + entry.st_size > _MAX_ENVIRONMENT_BYTES:
                raise BenchmarkEnvironmentError(
                    f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
                )
            copied = _copy_environment_file(
                source,
                path,
                relative,
                entry,
                destination_path,
            )
            file_count += 1
            total_bytes += copied
        if file_count == 0:
            raise BenchmarkEnvironmentError("environment source_dir contains no files")
        for directory, info in reversed(directory_metadata):
            try:
                directory.chmod(stat.S_IMODE(info.st_mode), follow_symlinks=False)
                os.utime(
                    directory,
                    ns=(info.st_atime_ns, info.st_mtime_ns),
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise BenchmarkEnvironmentError(
                    f"unable to preserve materialized environment directory metadata: {exc}"
                ) from exc
        scanned = _scan_environment(destination)
        if expected is not None and (
            scanned.source_sha256 != expected.source_sha256 or scanned.files != expected.files
        ):
            raise BenchmarkEnvironmentError("materialized environment does not match locked source")
        return scanned
    except BenchmarkEnvironmentError:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _copy_environment_tree_descriptor(
    source: Path,
    destination: Path,
    *,
    expected: BenchmarkEnvironmentLockEntry | None,
) -> _ScannedEnvironment:
    destination, parent_descriptor, parent_info = _open_materialization_parent(destination)
    root_descriptor: int | None = None
    created = False
    try:
        try:
            _stat_materialization_entry(parent_descriptor, destination.name)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to inspect environment materialization destination: {exc}"
            ) from exc
        else:
            raise BenchmarkEnvironmentError(
                "environment materialization destination already exists"
            )
        try:
            source_info = source.stat(follow_symlinks=False)
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to stat environment source_dir: {exc}"
            ) from exc
        if stat.S_ISLNK(source_info.st_mode) or not stat.S_ISDIR(source_info.st_mode):
            raise BenchmarkEnvironmentError("environment source_dir is not a directory")
        _materialization_parent_must_be_stable(
            destination,
            parent_descriptor,
            parent_info,
        )
        try:
            os.mkdir(destination.name, 0o700, dir_fd=parent_descriptor)
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to create benchmark environment destination: {exc}"
            ) from exc
        created = True
        root_expected = _stat_materialization_entry(parent_descriptor, destination.name)
        root_descriptor = _open_bound_materialized_directory(
            parent_descriptor,
            destination.name,
            root_expected,
            noun="materialized environment destination",
        )
        _materialization_parent_must_be_stable(
            destination,
            parent_descriptor,
            parent_info,
        )
        roots = _list_environment_directory(source, source, ".", source_info)
        directory_metadata: list[tuple[tuple[str, ...], os.stat_result]] = [((), source_info)]
        total_bytes = 0
        file_count = 0
        stack = list(reversed(roots))
        while stack:
            path = stack.pop()
            relative = path.relative_to(source).as_posix()
            _resolve_under_source(source, path, relative)
            try:
                entry = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise BenchmarkEnvironmentError(
                    f"unable to stat environment entry {relative}: {exc}"
                ) from exc
            if stat.S_ISLNK(entry.st_mode):
                raise BenchmarkEnvironmentError(f"environment contains symlink: {relative}")
            parts = tuple(PurePosixPath(relative).parts)
            if stat.S_ISDIR(entry.st_mode):
                children = _list_environment_directory(source, path, relative, entry)
                directory_descriptor = _open_materialized_directory(
                    root_descriptor,
                    parts,
                    create=True,
                )
                with suppress(OSError):
                    os.close(directory_descriptor)
                directory_metadata.append((parts, entry))
                stack.extend(reversed(children))
                continue
            if not stat.S_ISREG(entry.st_mode):
                raise BenchmarkEnvironmentError(
                    f"environment contains non-regular file: {relative}"
                )
            if file_count >= _MAX_FILES:
                raise BenchmarkEnvironmentError(f"environment exceeds {_MAX_FILES} files")
            if total_bytes + entry.st_size > _MAX_ENVIRONMENT_BYTES:
                raise BenchmarkEnvironmentError(
                    f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
                )
            copied = _copy_environment_file_at(
                source,
                path,
                relative,
                entry,
                root_descriptor,
                parts,
            )
            file_count += 1
            total_bytes += copied
        if file_count == 0:
            raise BenchmarkEnvironmentError("environment source_dir contains no files")
        for parts, info in reversed(directory_metadata):
            directory_descriptor = _open_materialized_directory(
                root_descriptor,
                parts,
                create=False,
            )
            try:
                os.fchmod(directory_descriptor, stat.S_IMODE(info.st_mode))
                os.utime(
                    directory_descriptor,
                    ns=(info.st_atime_ns, info.st_mtime_ns),
                )
            except OSError as exc:
                raise BenchmarkEnvironmentError(
                    f"unable to preserve materialized environment directory metadata: {exc}"
                ) from exc
            finally:
                with suppress(OSError):
                    os.close(directory_descriptor)
        scanned = _scan_materialized_environment(root_descriptor)
        if expected is not None and (
            scanned.source_sha256 != expected.source_sha256 or scanned.files != expected.files
        ):
            raise BenchmarkEnvironmentError("materialized environment does not match locked source")
        _materialization_parent_must_be_stable(
            destination,
            parent_descriptor,
            parent_info,
        )
        return scanned
    except BenchmarkEnvironmentError:
        if root_descriptor is not None:
            with suppress(OSError):
                os.close(root_descriptor)
            root_descriptor = None
        if created:
            _remove_materialized_tree_at(parent_descriptor, destination.name)
        raise
    finally:
        if root_descriptor is not None:
            with suppress(OSError):
                os.close(root_descriptor)
        with suppress(OSError):
            os.close(parent_descriptor)


def _copy_environment_tree(
    source: Path,
    destination: Path,
    *,
    expected: BenchmarkEnvironmentLockEntry | None = None,
) -> _ScannedEnvironment:
    if _MATERIALIZATION_DIR_FD_SUPPORTED:
        return _copy_environment_tree_descriptor(
            source,
            destination,
            expected=expected,
        )
    return _copy_environment_tree_path(
        source,
        destination,
        expected=expected,
    )


def _validated_benchmark_environment_suite(
    suite: BenchmarkEnvironmentSuite,
) -> BenchmarkEnvironmentSuite:
    return _revalidate_benchmark_environment_model(
        suite,
        BenchmarkEnvironmentSuite,
        noun="benchmark environment suite",
    )


def _validated_benchmark_environment_inputs(
    suite: BenchmarkEnvironmentSuite,
    lock: BenchmarkEnvironmentSuiteLock,
) -> tuple[BenchmarkEnvironmentSuite, BenchmarkEnvironmentSuiteLock]:
    validated_suite = _validated_benchmark_environment_suite(suite)
    validated_lock = _revalidate_benchmark_environment_model(
        lock,
        BenchmarkEnvironmentSuiteLock,
        noun="benchmark environment lock",
    )
    return validated_suite, validated_lock


def seal_benchmark_environment_suite(
    suite: BenchmarkEnvironmentSuite,
    *,
    root: Path,
) -> BenchmarkEnvironmentSuiteLock:
    """Generate a deterministic lock from all source trees under the declared root."""
    suite = _validated_benchmark_environment_suite(suite)
    resolved_root = _safe_root(root)
    entries: list[BenchmarkEnvironmentLockEntry] = []
    for environment in suite.environments:
        source = _resolve_relative(resolved_root, environment.source_dir, "environment source_dir")
        scanned = _scan_environment(source)
        entries.append(
            BenchmarkEnvironmentLockEntry(
                id=environment.id,
                kind=environment.kind,
                source_sha256=scanned.source_sha256,
                file_count=len(scanned.files),
                total_bytes=scanned.total_bytes,
                files=scanned.files,
            )
        )
    return BenchmarkEnvironmentSuiteLock(
        suite_id=suite.id,
        suite_sha256=_benchmark_environment_suite_sha256_validated(suite),
        environments=entries,
    )


def _verify_benchmark_environment_suite_validated(
    suite: BenchmarkEnvironmentSuite,
    lock: BenchmarkEnvironmentSuiteLock,
    *,
    root: Path,
) -> BenchmarkEnvironmentVerification:
    if lock.suite_id != suite.id:
        raise BenchmarkEnvironmentError("environment lock suite_id does not match suite")
    expected_suite_sha256 = _benchmark_environment_suite_sha256_validated(suite)
    if lock.suite_sha256 != expected_suite_sha256:
        raise BenchmarkEnvironmentError("environment lock suite_sha256 does not match suite")
    locked = {entry.id: entry for entry in lock.environments}
    expected_ids = [environment.id for environment in suite.environments]
    locked_ids = [entry.id for entry in lock.environments]
    if locked_ids != expected_ids:
        raise BenchmarkEnvironmentError(
            "environment lock entries must match suite environments in order"
        )

    resolved_root = _safe_root(root)
    environment_sha256: dict[str, str] = {}
    file_count = 0
    total_bytes = 0
    for environment in suite.environments:
        entry = locked[environment.id]
        if entry.kind is not environment.kind:
            raise BenchmarkEnvironmentError(
                f"environment lock kind does not match suite for {environment.id!r}"
            )
        source = _resolve_relative(resolved_root, environment.source_dir, "environment source_dir")
        scanned = _scan_environment(source)
        if scanned.source_sha256 != entry.source_sha256:
            raise BenchmarkEnvironmentError(
                f"environment source digest does not match lock for {environment.id!r}"
            )
        if scanned.files != entry.files:
            raise BenchmarkEnvironmentError(
                f"environment file manifest does not match lock for {environment.id!r}"
            )
        environment_sha256[environment.id] = scanned.source_sha256
        file_count += len(scanned.files)
        total_bytes += scanned.total_bytes
    return BenchmarkEnvironmentVerification(
        suite_id=suite.id,
        suite_sha256=expected_suite_sha256,
        environment_count=len(suite.environments),
        file_count=file_count,
        total_bytes=total_bytes,
        environment_sha256=environment_sha256,
        verified=True,
    )


def verify_benchmark_environment_suite(
    suite: BenchmarkEnvironmentSuite,
    lock: BenchmarkEnvironmentSuiteLock,
    *,
    root: Path,
) -> BenchmarkEnvironmentVerification:
    """Verify that the suite spec and every source tree exactly match the lock."""
    suite, lock = _validated_benchmark_environment_inputs(suite, lock)
    return _verify_benchmark_environment_suite_validated(suite, lock, root=root)


def materialize_benchmark_environment(
    suite: BenchmarkEnvironmentSuite,
    lock: BenchmarkEnvironmentSuiteLock,
    environment_id: str,
    *,
    root: Path,
    destination: Path,
) -> BenchmarkEnvironmentVerification:
    """Copy one verified environment tree to a new empty destination and re-verify bytes."""
    suite, lock = _validated_benchmark_environment_inputs(suite, lock)
    verification = _verify_benchmark_environment_suite_validated(suite, lock, root=root)
    by_id = {environment.id: environment for environment in suite.environments}
    environment = by_id.get(environment_id)
    if environment is None:
        raise BenchmarkEnvironmentError(f"unknown benchmark environment {environment_id!r}")
    resolved_root = _safe_root(root)
    source = _resolve_relative(resolved_root, environment.source_dir, "environment source_dir")
    locked = {entry.id: entry for entry in lock.environments}[environment_id]
    _copy_environment_tree(
        source,
        destination,
        expected=locked,
    )
    return verification


def load_benchmark_environment_suite(path: Path) -> BenchmarkEnvironmentSuite:
    """Load a bounded strict JSON/YAML environment suite."""
    payload = _read_mapping(path, noun="benchmark environment suite")
    try:
        return BenchmarkEnvironmentSuite.model_validate(payload)
    except ValueError as exc:
        raise BenchmarkEnvironmentError(str(exc)) from exc


def load_benchmark_environment_lock(path: Path) -> BenchmarkEnvironmentSuiteLock:
    """Load a bounded strict JSON/YAML generated environment lock."""
    payload = _read_mapping(path, noun="benchmark environment lock")
    try:
        return BenchmarkEnvironmentSuiteLock.model_validate(payload)
    except ValueError as exc:
        raise BenchmarkEnvironmentError(str(exc)) from exc
