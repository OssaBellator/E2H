"""Deterministic release toolchain evidence derived from reviewed repository inputs."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import stat
import tomllib
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from e2h.release_source import ReleaseSourceError, source_tree_sha256

_MAX_INPUT_BYTES = 16 * 1024 * 1024
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_COMMIT_PATTERN = r"^[0-9a-f]{40}$"
_VERSION_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9._+-]*)?$"
_RUNNER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$"
_EXACT_UV_RE = re.compile(r"^==(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9._+-]*)?)$")
_EXACT_HATCHLING_RE = re.compile(
    r"^hatchling==(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[A-Za-z0-9._+-]*)?)$"
)
_SOURCE_COMMIT_RE = re.compile(_COMMIT_PATTERN)
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd


class ReleaseToolchainError(ValueError):
    """Raised when release toolchain evidence cannot be derived safely."""


class ReleaseToolchainEvidence(BaseModel):
    """Repository-bound toolchain identity embedded in release manifest metadata."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    source_commit: str = Field(pattern=_COMMIT_PATTERN)
    runner_generation: str = Field(pattern=_RUNNER_PATTERN)
    python_version: str = Field(pattern=_VERSION_PATTERN)
    uv_required_version: str = Field(pattern=_VERSION_PATTERN)
    build_backend: Literal["hatchling.build"] = "hatchling.build"
    build_backend_version: str = Field(pattern=_VERSION_PATTERN)
    source_date_epoch: int = Field(ge=0)
    uv_toml_sha256: str = Field(pattern=_SHA256_PATTERN)
    pyproject_sha256: str = Field(pattern=_SHA256_PATTERN)
    uv_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    build_constraints_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_tree_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)


class ReleaseToolchainVerification(BaseModel):
    """Proof that manifest toolchain evidence matches one source tree."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    evidence: ReleaseToolchainEvidence
    source_inputs_verified: Literal[True] = True
    source_tree_verified: bool = False
    source_commit_verified: bool = False
    verified: Literal[True] = True


class _ReleaseToolchainSourceInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uv_required_version: str = Field(pattern=_VERSION_PATTERN)
    build_backend: Literal["hatchling.build"] = "hatchling.build"
    build_backend_version: str = Field(pattern=_VERSION_PATTERN)
    uv_toml_sha256: str = Field(pattern=_SHA256_PATTERN)
    pyproject_sha256: str = Field(pattern=_SHA256_PATTERN)
    uv_lock_sha256: str = Field(pattern=_SHA256_PATTERN)
    build_constraints_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_tree_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def _open_toolchain_root(root: Path) -> tuple[Path, int, os.stat_result]:
    try:
        expected = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseToolchainError(f"unable to inspect toolchain root: {exc}") from exc
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
        raise ReleaseToolchainError("toolchain root must be a real directory")
    try:
        resolved = root.resolve(strict=True)
        current = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseToolchainError(f"unable to inspect toolchain root: {exc}") from exc
    if _stat_identity(current) != _stat_identity(expected):
        raise ReleaseToolchainError("toolchain root changed while resolving")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise ReleaseToolchainError(f"unable to open toolchain root: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise ReleaseToolchainError("toolchain root is no longer a directory")
        if _stat_identity(opened) != _stat_identity(current):
            raise ReleaseToolchainError("toolchain root changed while opening")
        return resolved, descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _toolchain_root_must_be_stable(
    root: Path,
    descriptor: int,
    opened: os.stat_result,
) -> None:
    try:
        after = os.fstat(descriptor)
        current = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseToolchainError(f"unable to restat toolchain root: {exc}") from exc
    if _stat_identity(after) != _stat_identity(opened) or _stat_identity(current) != _stat_identity(
        opened
    ):
        raise ReleaseToolchainError("toolchain root changed while reading inputs")


def _stat_toolchain_input(root_descriptor: int, path: Path) -> os.stat_result:
    try:
        if _STAT_SUPPORTS_DIR_FD:
            return os.stat(
                path.name,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
        return path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseToolchainError(f"unable to stat toolchain input {path.name}: {exc}") from exc


def _read_regular_file(root_descriptor: int, path: Path) -> bytes:
    expected = _stat_toolchain_input(root_descriptor, path)
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
        raise ReleaseToolchainError(f"toolchain input must be a regular file: {path.name}")
    if expected.st_size <= 0:
        raise ReleaseToolchainError(f"toolchain input must not be empty: {path.name}")
    if expected.st_size > _MAX_INPUT_BYTES:
        raise ReleaseToolchainError(
            f"toolchain input exceeds {_MAX_INPUT_BYTES} bytes: {path.name}"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = (
            os.open(path.name, flags, dir_fd=root_descriptor)
            if _OPEN_SUPPORTS_DIR_FD
            else os.open(path, flags)
        )
    except OSError as exc:
        raise ReleaseToolchainError(f"unable to read toolchain input {path.name}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ReleaseToolchainError(f"toolchain input must be a regular file: {path.name}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseToolchainError(f"toolchain input changed while opening: {path.name}")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_INPUT_BYTES + 1)
        after = os.fstat(descriptor)
        current = _stat_toolchain_input(root_descriptor, path)
        if len(raw) > _MAX_INPUT_BYTES:
            raise ReleaseToolchainError(
                f"toolchain input exceeds {_MAX_INPUT_BYTES} bytes: {path.name}"
            )
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or len(raw) != opened.st_size
        ):
            raise ReleaseToolchainError(f"toolchain input changed while reading: {path.name}")
        return raw
    except OSError as exc:
        raise ReleaseToolchainError(f"unable to read toolchain input {path.name}: {exc}") from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _parse_toml(raw: bytes, *, noun: str) -> dict[str, object]:
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseToolchainError(f"invalid {noun}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ReleaseToolchainError(f"invalid {noun}: root must be a table")
    return parsed


def _uv_required_version(parsed: dict[str, object]) -> str:
    value = parsed.get("required-version")
    if not isinstance(value, str):
        raise ReleaseToolchainError("uv.toml must define required-version")
    match = _EXACT_UV_RE.fullmatch(value)
    if match is None:
        raise ReleaseToolchainError("uv.toml required-version must use one exact == version")
    return match.group("version")


def _hatchling_version(parsed: dict[str, object]) -> str:
    build_system = parsed.get("build-system")
    if not isinstance(build_system, dict):
        raise ReleaseToolchainError("pyproject.toml must define build-system")
    if build_system.get("build-backend") != "hatchling.build":
        raise ReleaseToolchainError("pyproject.toml build backend must be hatchling.build")
    requires = build_system.get("requires")
    if not isinstance(requires, list) or len(requires) != 1 or not isinstance(requires[0], str):
        raise ReleaseToolchainError("pyproject.toml build-system requires must contain one entry")
    match = _EXACT_HATCHLING_RE.fullmatch(requires[0])
    if match is None:
        raise ReleaseToolchainError("Hatchling build requirement must use one exact == version")
    return match.group("version")


def _constraints_hatchling_version(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseToolchainError("build-constraints.txt must be UTF-8") from exc
    matches: list[str] = re.findall(r"(?m)^hatchling==([^\s\\]+)\s*\\$", text)
    if len(matches) != 1:
        raise ReleaseToolchainError(
            "build-constraints.txt must contain one exact hashed Hatchling requirement"
        )
    return matches[0]


def _collect_release_toolchain_source_inputs(
    root: Path,
    *,
    include_source_tree: bool,
) -> _ReleaseToolchainSourceInputs:
    root, root_descriptor, root_info = _open_toolchain_root(root)
    input_names = ("uv.toml", "pyproject.toml", "uv.lock", "build-constraints.txt")
    try:
        inputs = {name: _read_regular_file(root_descriptor, root / name) for name in input_names}
        _toolchain_root_must_be_stable(root, root_descriptor, root_info)
        uv_config = _parse_toml(inputs["uv.toml"], noun="uv.toml")
        pyproject = _parse_toml(inputs["pyproject.toml"], noun="pyproject.toml")
        uv_version = _uv_required_version(uv_config)
        hatchling_version = _hatchling_version(pyproject)
        constrained_hatchling = _constraints_hatchling_version(inputs["build-constraints.txt"])
        if constrained_hatchling != hatchling_version:
            raise ReleaseToolchainError(
                "build-constraints.txt Hatchling version must match pyproject.toml"
            )

        tree_digest: str | None = None
        if include_source_tree:
            try:
                tree_digest = source_tree_sha256(root)
            except ReleaseSourceError as exc:
                raise ReleaseToolchainError(
                    f"unable to identify release source tree: {exc}"
                ) from exc
            repeated = {
                name: _read_regular_file(root_descriptor, root / name) for name in input_names
            }
            if repeated != inputs:
                raise ReleaseToolchainError(
                    "toolchain inputs changed while identifying release source tree"
                )
        _toolchain_root_must_be_stable(root, root_descriptor, root_info)
    finally:
        with suppress(OSError):
            os.close(root_descriptor)

    return _ReleaseToolchainSourceInputs(
        uv_required_version=uv_version,
        build_backend_version=hatchling_version,
        uv_toml_sha256=hashlib.sha256(inputs["uv.toml"]).hexdigest(),
        pyproject_sha256=hashlib.sha256(inputs["pyproject.toml"]).hexdigest(),
        uv_lock_sha256=hashlib.sha256(inputs["uv.lock"]).hexdigest(),
        build_constraints_sha256=hashlib.sha256(inputs["build-constraints.txt"]).hexdigest(),
        source_tree_sha256=tree_digest,
    )


def collect_release_toolchain_evidence(
    root: Path,
    *,
    source_commit: str,
    runner_generation: str,
    source_date_epoch: int,
) -> ReleaseToolchainEvidence:
    """Collect deterministic toolchain evidence from one reviewed source tree."""
    source = _collect_release_toolchain_source_inputs(root, include_source_tree=True)
    try:
        return ReleaseToolchainEvidence(
            source_commit=source_commit,
            runner_generation=runner_generation,
            python_version=platform.python_version(),
            uv_required_version=source.uv_required_version,
            build_backend=source.build_backend,
            build_backend_version=source.build_backend_version,
            source_date_epoch=source_date_epoch,
            uv_toml_sha256=source.uv_toml_sha256,
            pyproject_sha256=source.pyproject_sha256,
            uv_lock_sha256=source.uv_lock_sha256,
            build_constraints_sha256=source.build_constraints_sha256,
            source_tree_sha256=source.source_tree_sha256,
        )
    except ValueError as exc:
        raise ReleaseToolchainError(f"invalid release toolchain evidence: {exc}") from exc


def release_toolchain_evidence_from_metadata(
    metadata: Mapping[str, object],
) -> ReleaseToolchainEvidence:
    """Load strict release-toolchain evidence from manifest metadata."""
    raw = metadata.get("release_toolchain")
    if raw is None:
        raise ReleaseToolchainError("release manifest has no metadata.release_toolchain evidence")
    try:
        return ReleaseToolchainEvidence.model_validate(raw)
    except ValueError as exc:
        raise ReleaseToolchainError(f"invalid metadata.release_toolchain evidence: {exc}") from exc


def verify_release_toolchain_evidence(
    metadata: Mapping[str, object],
    root: Path,
    *,
    expected_source_commit: str | None = None,
) -> ReleaseToolchainVerification:
    """Verify source-controlled toolchain evidence against one source tree."""
    evidence = release_toolchain_evidence_from_metadata(metadata)
    source_tree_present = evidence.source_tree_sha256 is not None
    source = _collect_release_toolchain_source_inputs(
        root,
        include_source_tree=source_tree_present,
    )
    comparable_fields = (
        "uv_required_version",
        "build_backend",
        "build_backend_version",
        "uv_toml_sha256",
        "pyproject_sha256",
        "uv_lock_sha256",
        "build_constraints_sha256",
    )
    mismatches = [
        field for field in comparable_fields if getattr(evidence, field) != getattr(source, field)
    ]
    if mismatches:
        raise ReleaseToolchainError(
            "release toolchain evidence does not match source tree: " + ", ".join(mismatches)
        )

    source_tree_verified = False
    if source_tree_present:
        if evidence.source_tree_sha256 != source.source_tree_sha256:
            raise ReleaseToolchainError(
                "release toolchain evidence does not match source tree: source_tree_sha256"
            )
        source_tree_verified = True

    source_commit_verified = False
    if expected_source_commit is not None:
        if _SOURCE_COMMIT_RE.fullmatch(expected_source_commit) is None:
            raise ReleaseToolchainError(
                "expected source commit must be a lowercase 40-character SHA"
            )
        if evidence.source_commit != expected_source_commit:
            raise ReleaseToolchainError(
                "release toolchain source commit does not match expected source commit"
            )
        source_commit_verified = True

    return ReleaseToolchainVerification(
        evidence=evidence,
        source_tree_verified=source_tree_verified,
        source_commit_verified=source_commit_verified,
    )


def release_toolchain_metadata(evidence: ReleaseToolchainEvidence) -> dict[str, object]:
    """Render toolchain evidence into the release manifest's existing metadata field."""
    return {"release_toolchain": evidence.model_dump(mode="json")}
