"""Content-addressed reproducible environments for E2H community benchmarks."""

from __future__ import annotations

import hashlib
import json
import shutil
import stat
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


def benchmark_environment_suite_sha256(suite: BenchmarkEnvironmentSuite) -> str:
    """Return the canonical identity of a human-authored environment suite."""
    return hashlib.sha256(_canonical_json_bytes(suite.model_dump(mode="json"))).hexdigest()


def _tree_digest(files: list[BenchmarkEnvironmentFile]) -> str:
    material = [item.model_dump(mode="json") for item in files]
    return hashlib.sha256(_canonical_json_bytes(material)).hexdigest()


def _scan_environment(source: Path) -> _ScannedEnvironment:
    if not source.is_dir():
        raise BenchmarkEnvironmentError("environment source_dir is not a directory")
    files: list[BenchmarkEnvironmentFile] = []
    total_bytes = 0
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink():
            raise BenchmarkEnvironmentError(f"environment contains symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise BenchmarkEnvironmentError(f"environment contains non-regular file: {relative}")
        try:
            before = path.stat()
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to stat environment file {relative}: {exc}"
            ) from exc
        size = before.st_size
        total_bytes += size
        if len(files) >= _MAX_FILES:
            raise BenchmarkEnvironmentError(f"environment exceeds {_MAX_FILES} files")
        if total_bytes > _MAX_ENVIRONMENT_BYTES:
            raise BenchmarkEnvironmentError(
                f"environment exceeds {_MAX_ENVIRONMENT_BYTES} total bytes"
            )
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to hash environment file {relative}: {exc}"
            ) from exc
        try:
            after = path.stat()
        except OSError as exc:
            raise BenchmarkEnvironmentError(
                f"unable to restat environment file {relative}: {exc}"
            ) from exc
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after:
            raise BenchmarkEnvironmentError(f"environment file changed while hashing: {relative}")
        mode = after.st_mode
        files.append(
            BenchmarkEnvironmentFile(
                path=relative,
                sha256=digest.hexdigest(),
                size=size,
                executable=bool(mode & stat.S_IXUSR),
            )
        )
    if not files:
        raise BenchmarkEnvironmentError("environment source_dir contains no files")
    return _ScannedEnvironment(
        files=files,
        source_sha256=_tree_digest(files),
        total_bytes=total_bytes,
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
        suite_sha256=benchmark_environment_suite_sha256(suite),
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
    expected_suite_sha256 = benchmark_environment_suite_sha256(suite)
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
    resolved_destination = destination.resolve()
    if resolved_destination.exists():
        raise BenchmarkEnvironmentError("environment materialization destination already exists")
    try:
        shutil.copytree(source, resolved_destination, symlinks=True, copy_function=shutil.copy2)
    except OSError as exc:
        raise BenchmarkEnvironmentError(
            f"unable to materialize benchmark environment: {exc}"
        ) from exc
    locked = {entry.id: entry for entry in lock.environments}[environment_id]
    scanned = _scan_environment(resolved_destination)
    if scanned.source_sha256 != locked.source_sha256 or scanned.files != locked.files:
        shutil.rmtree(resolved_destination, ignore_errors=True)
        raise BenchmarkEnvironmentError("materialized environment does not match locked source")
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
