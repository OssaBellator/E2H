"""Deterministic release toolchain evidence derived from reviewed repository inputs."""

from __future__ import annotations

import hashlib
import platform
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class ReleaseToolchainVerification(BaseModel):
    """Proof that manifest toolchain evidence matches one source tree."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    evidence: ReleaseToolchainEvidence
    source_inputs_verified: Literal[True] = True
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


def _read_regular_file(path: Path) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise ReleaseToolchainError(f"toolchain input must be a regular file: {path.name}")
        size = path.stat().st_size
        if size <= 0:
            raise ReleaseToolchainError(f"toolchain input must not be empty: {path.name}")
        if size > _MAX_INPUT_BYTES:
            raise ReleaseToolchainError(
                f"toolchain input exceeds {_MAX_INPUT_BYTES} bytes: {path.name}"
            )
        return path.read_bytes()
    except OSError as exc:
        raise ReleaseToolchainError(f"unable to read toolchain input {path.name}: {exc}") from exc


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


def _collect_release_toolchain_source_inputs(root: Path) -> _ReleaseToolchainSourceInputs:
    try:
        if root.is_symlink() or not root.is_dir():
            raise ReleaseToolchainError("toolchain root must be a real directory")
    except OSError as exc:
        raise ReleaseToolchainError(f"unable to inspect toolchain root: {exc}") from exc

    inputs = {
        "uv.toml": _read_regular_file(root / "uv.toml"),
        "pyproject.toml": _read_regular_file(root / "pyproject.toml"),
        "uv.lock": _read_regular_file(root / "uv.lock"),
        "build-constraints.txt": _read_regular_file(root / "build-constraints.txt"),
    }
    uv_config = _parse_toml(inputs["uv.toml"], noun="uv.toml")
    pyproject = _parse_toml(inputs["pyproject.toml"], noun="pyproject.toml")
    uv_version = _uv_required_version(uv_config)
    hatchling_version = _hatchling_version(pyproject)
    constrained_hatchling = _constraints_hatchling_version(inputs["build-constraints.txt"])
    if constrained_hatchling != hatchling_version:
        raise ReleaseToolchainError(
            "build-constraints.txt Hatchling version must match pyproject.toml"
        )
    return _ReleaseToolchainSourceInputs(
        uv_required_version=uv_version,
        build_backend_version=hatchling_version,
        uv_toml_sha256=hashlib.sha256(inputs["uv.toml"]).hexdigest(),
        pyproject_sha256=hashlib.sha256(inputs["pyproject.toml"]).hexdigest(),
        uv_lock_sha256=hashlib.sha256(inputs["uv.lock"]).hexdigest(),
        build_constraints_sha256=hashlib.sha256(inputs["build-constraints.txt"]).hexdigest(),
    )


def collect_release_toolchain_evidence(
    root: Path,
    *,
    source_commit: str,
    runner_generation: str,
    source_date_epoch: int,
) -> ReleaseToolchainEvidence:
    """Collect deterministic toolchain evidence from one reviewed source tree."""
    source = _collect_release_toolchain_source_inputs(root)
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
    source = _collect_release_toolchain_source_inputs(root)
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
        source_commit_verified=source_commit_verified,
    )


def release_toolchain_metadata(evidence: ReleaseToolchainEvidence) -> dict[str, object]:
    """Render toolchain evidence into the release manifest's existing metadata field."""
    return {"release_toolchain": evidence.model_dump(mode="json")}
