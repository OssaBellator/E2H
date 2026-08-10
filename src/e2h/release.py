"""Deterministic release artifact sealing and verification."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tarfile
import zipfile
from contextlib import suppress
from email.parser import BytesParser
from email.policy import default as email_policy
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import _validate_json_compatible

_MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
_MAX_ARTIFACTS = 32
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,254}$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd


class ReleaseIntegrityError(ValueError):
    """Raised when release artifacts cannot be safely sealed or verified."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReleaseArtifactKind(StrEnum):
    WHEEL = "wheel"
    SDIST = "sdist"


class ReleaseArtifact(StrictModel):
    """Content identity and embedded package identity for one distribution artifact."""

    filename: str = Field(min_length=1, max_length=255)
    kind: ReleaseArtifactKind
    size_bytes: int = Field(ge=1, le=_MAX_ARTIFACT_BYTES)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    package_name: str = Field(min_length=1, max_length=255)
    package_version: str = Field(min_length=1, max_length=255)

    @field_validator("filename")
    @classmethod
    def filename_must_be_portable(cls, value: str) -> str:
        if _FILENAME_RE.fullmatch(value) is None:
            raise ValueError("release artifact filename must be a portable basename")
        return value


class ReleaseManifest(StrictModel):
    """Deterministic manifest for a complete wheel/sdist release set."""

    schema_version: Literal["0.1"] = "0.1"
    project: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=255)
    artifacts: list[ReleaseArtifact] = Field(min_length=1, max_length=_MAX_ARTIFACTS)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_canonical_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        encoded = _canonical_json_bytes(value)
        if len(encoded) > 65_536:
            raise ValueError("release manifest metadata exceeds 65536 bytes")
        return value

    @model_validator(mode="after")
    def artifacts_must_be_complete_and_consistent(self) -> ReleaseManifest:
        filenames = [artifact.filename for artifact in self.artifacts]
        if filenames != sorted(filenames):
            raise ValueError("release artifacts must be sorted by filename")
        if len(filenames) != len(set(filenames)):
            raise ValueError("release artifact filenames must be unique")
        normalized_project = _normalize_project_name(self.project)
        for artifact in self.artifacts:
            if _normalize_project_name(artifact.package_name) != normalized_project:
                raise ValueError("artifact package name must match manifest project")
            if artifact.package_version != self.version:
                raise ValueError("artifact package version must match manifest version")
        kinds = {artifact.kind for artifact in self.artifacts}
        if kinds != {ReleaseArtifactKind.WHEEL, ReleaseArtifactKind.SDIST}:
            raise ValueError("release manifest must contain exactly a wheel and an sdist kind")
        return self


class ReleaseVerification(StrictModel):
    """Digest-only proof that a directory matches one exact release manifest."""

    schema_version: Literal["0.1"] = "0.1"
    project: str
    version: str
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_count: int = Field(ge=1, le=_MAX_ARTIFACTS)
    total_bytes: int = Field(ge=1)
    artifacts: dict[str, str]
    verified: bool = True


class _PackageIdentity(StrictModel):
    name: str
    version: str


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


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        _validate_json_compatible(value)
        _validate_json_object_keys(value)
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


def _normalize_project_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _artifact_kind(filename: str) -> ReleaseArtifactKind:
    if filename.endswith(".whl"):
        return ReleaseArtifactKind.WHEEL
    if filename.endswith(".tar.gz"):
        return ReleaseArtifactKind.SDIST
    raise ReleaseIntegrityError(f"unsupported release artifact: {filename}")


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _open_release_directory(directory: Path) -> tuple[int, os.stat_result]:
    try:
        expected = directory.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseIntegrityError(f"unable to read release directory: {exc}") from exc
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
        raise ReleaseIntegrityError("release directory must be a real directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError as exc:
        raise ReleaseIntegrityError(f"unable to open release directory: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise ReleaseIntegrityError("release directory is no longer a directory")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseIntegrityError("release directory changed while opening")
        return descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _stat_release_entry(directory_descriptor: int, path: Path) -> os.stat_result:
    try:
        if os.stat in os.supports_dir_fd:
            return os.stat(
                path.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        return path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseIntegrityError(f"unable to stat release artifact {path.name}: {exc}") from exc


def _release_directory_must_be_stable(
    directory: Path,
    descriptor: int,
    opened: os.stat_result,
) -> None:
    try:
        after = os.fstat(descriptor)
        current = directory.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseIntegrityError(f"unable to restat release directory: {exc}") from exc
    if _stat_identity(after) != _stat_identity(opened) or _stat_identity(current) != _stat_identity(
        opened
    ):
        raise ReleaseIntegrityError("release directory changed during sealing")


def _distribution_entries(
    directory: Path,
    descriptor: int,
    opened: os.stat_result,
) -> list[tuple[Path, os.stat_result]]:
    try:
        names = (
            sorted(os.listdir(descriptor))
            if os.listdir in os.supports_fd
            else sorted(os.listdir(directory))
        )
    except OSError as exc:
        raise ReleaseIntegrityError(f"unable to read release directory: {exc}") from exc
    _release_directory_must_be_stable(directory, descriptor, opened)
    if not names:
        raise ReleaseIntegrityError("release directory is empty")
    if len(names) > _MAX_ARTIFACTS:
        raise ReleaseIntegrityError(f"release directory exceeds {_MAX_ARTIFACTS} entries")
    entries: list[tuple[Path, os.stat_result]] = []
    for name in names:
        path = directory / name
        entry = _stat_release_entry(descriptor, path)
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
            raise ReleaseIntegrityError(
                f"release directory may contain only regular distribution files: {name}"
            )
        _artifact_kind(name)
        entries.append((path, entry))
    _release_directory_must_be_stable(directory, descriptor, opened)
    return entries


def _read_regular_artifact(
    directory_descriptor: int,
    path: Path,
    expected: os.stat_result,
) -> bytes:
    if expected.st_size <= 0:
        raise ReleaseIntegrityError(f"release artifact must not be empty: {path.name}")
    if expected.st_size > _MAX_ARTIFACT_BYTES:
        raise ReleaseIntegrityError(
            f"release artifact exceeds {_MAX_ARTIFACT_BYTES} bytes: {path.name}"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        if os.open in os.supports_dir_fd:
            descriptor = os.open(path.name, flags, dir_fd=directory_descriptor)
        else:
            descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseIntegrityError(f"unable to open release artifact {path.name}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ReleaseIntegrityError(f"release artifact must be a regular file: {path.name}")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseIntegrityError(f"release artifact changed while opening: {path.name}")
        if opened.st_size <= 0:
            raise ReleaseIntegrityError(f"release artifact must not be empty: {path.name}")
        if opened.st_size > _MAX_ARTIFACT_BYTES:
            raise ReleaseIntegrityError(
                f"release artifact exceeds {_MAX_ARTIFACT_BYTES} bytes: {path.name}"
            )
        data = bytearray()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                data.extend(chunk)
                if len(data) > _MAX_ARTIFACT_BYTES:
                    raise ReleaseIntegrityError(
                        f"release artifact exceeds {_MAX_ARTIFACT_BYTES} bytes: {path.name}"
                    )
        after = os.fstat(descriptor)
        current = _stat_release_entry(directory_descriptor, path)
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or len(data) != opened.st_size
        ):
            raise ReleaseIntegrityError(f"release artifact changed while reading: {path.name}")
        return bytes(data)
    except OSError as exc:
        raise ReleaseIntegrityError(f"unable to read release artifact {path.name}: {exc}") from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _parse_core_metadata(raw: bytes, *, noun: str) -> _PackageIdentity:
    if len(raw) > _MAX_METADATA_BYTES:
        raise ReleaseIntegrityError(f"{noun} metadata exceeds {_MAX_METADATA_BYTES} bytes")
    message = BytesParser(policy=email_policy).parsebytes(raw)
    name = message.get("Name")
    version = message.get("Version")
    if not isinstance(name, str) or not name.strip():
        raise ReleaseIntegrityError(f"{noun} metadata is missing Name")
    if not isinstance(version, str) or not version.strip():
        raise ReleaseIntegrityError(f"{noun} metadata is missing Version")
    return _PackageIdentity(name=name.strip(), version=version.strip())


def _wheel_identity(raw: bytes, *, filename: str) -> _PackageIdentity:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            metadata_names = [
                name
                for name in archive.namelist()
                if name.count("/") == 1 and name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_names) != 1:
                raise ReleaseIntegrityError("wheel must contain exactly one .dist-info/METADATA")
            info = archive.getinfo(metadata_names[0])
            if info.file_size > _MAX_METADATA_BYTES:
                raise ReleaseIntegrityError(f"wheel metadata exceeds {_MAX_METADATA_BYTES} bytes")
            metadata = archive.read(info)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ReleaseIntegrityError(f"invalid wheel archive {filename}: {exc}") from exc
    return _parse_core_metadata(metadata, noun="wheel")


def _sdist_identity(raw: bytes, *, filename: str) -> _PackageIdentity:
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            members = [
                member
                for member in archive.getmembers()
                if member.name.count("/") == 1 and member.name.endswith("/PKG-INFO")
            ]
            if len(members) != 1:
                raise ReleaseIntegrityError("sdist must contain exactly one top-level PKG-INFO")
            member = members[0]
            if not member.isfile():
                raise ReleaseIntegrityError("sdist PKG-INFO must be a regular file")
            if member.size > _MAX_METADATA_BYTES:
                raise ReleaseIntegrityError(f"sdist metadata exceeds {_MAX_METADATA_BYTES} bytes")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ReleaseIntegrityError("unable to read sdist PKG-INFO")
            metadata = extracted.read(_MAX_METADATA_BYTES + 1)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseIntegrityError(f"invalid sdist archive {filename}: {exc}") from exc
    return _parse_core_metadata(metadata, noun="sdist")


def _artifact_from_entry(
    directory_descriptor: int,
    path: Path,
    expected: os.stat_result,
) -> ReleaseArtifact:
    if _FILENAME_RE.fullmatch(path.name) is None:
        raise ReleaseIntegrityError(
            f"release artifact filename must be a portable basename: {path.name}"
        )
    kind = _artifact_kind(path.name)
    raw = _read_regular_artifact(directory_descriptor, path, expected)
    identity = (
        _wheel_identity(raw, filename=path.name)
        if kind is ReleaseArtifactKind.WHEEL
        else _sdist_identity(raw, filename=path.name)
    )
    return ReleaseArtifact(
        filename=path.name,
        kind=kind,
        size_bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        package_name=identity.name,
        package_version=identity.version,
    )


def seal_release_artifacts(
    directory: Path,
    *,
    expected_project: str | None = None,
    expected_version: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ReleaseManifest:
    """Seal one exact wheel/sdist directory into a deterministic manifest."""
    directory_descriptor, opened_directory = _open_release_directory(directory)
    try:
        entries = _distribution_entries(directory, directory_descriptor, opened_directory)
        artifacts = [
            _artifact_from_entry(directory_descriptor, path, expected) for path, expected in entries
        ]
        _release_directory_must_be_stable(directory, directory_descriptor, opened_directory)
    finally:
        with suppress(OSError):
            os.close(directory_descriptor)
    project = artifacts[0].package_name
    version = artifacts[0].package_version
    if expected_project is not None and _normalize_project_name(project) != _normalize_project_name(
        expected_project
    ):
        raise ReleaseIntegrityError(
            f"artifact project {project!r} does not match expected project {expected_project!r}"
        )
    if expected_version is not None and version != expected_version:
        raise ReleaseIntegrityError(
            f"artifact version {version!r} does not match expected version {expected_version!r}"
        )
    try:
        return ReleaseManifest(
            project=expected_project or project,
            version=expected_version or version,
            artifacts=artifacts,
            metadata=metadata or {},
        )
    except ValueError as exc:
        raise ReleaseIntegrityError(f"release artifacts are inconsistent: {exc}") from exc


def _validated_release_manifest(manifest: ReleaseManifest) -> ReleaseManifest:
    if type(manifest) is not ReleaseManifest:
        raise ReleaseIntegrityError(
            f"invalid release manifest: expected ReleaseManifest, got {type(manifest).__name__}"
        )
    try:
        payload = manifest.model_dump(mode="python", warnings="none")
        return ReleaseManifest.model_validate(payload)
    except ValueError as exc:
        raise ReleaseIntegrityError(f"invalid release manifest: {exc}") from exc


def _release_manifest_sha256_validated(manifest: ReleaseManifest) -> str:
    payload = manifest.model_dump(mode="json")
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def release_manifest_sha256(manifest: ReleaseManifest) -> str:
    """Return the canonical identity of a release manifest."""
    return _release_manifest_sha256_validated(_validated_release_manifest(manifest))


def verify_release_artifacts(
    manifest: ReleaseManifest,
    directory: Path,
) -> ReleaseVerification:
    """Verify that a directory exactly matches one sealed release manifest."""
    validated = _validated_release_manifest(manifest)
    actual = seal_release_artifacts(
        directory,
        expected_project=validated.project,
        expected_version=validated.version,
        metadata=validated.metadata,
    )
    expected_by_name = {artifact.filename: artifact for artifact in validated.artifacts}
    actual_by_name = {artifact.filename: artifact for artifact in actual.artifacts}
    if set(expected_by_name) != set(actual_by_name):
        missing = sorted(set(expected_by_name) - set(actual_by_name))
        extra = sorted(set(actual_by_name) - set(expected_by_name))
        raise ReleaseIntegrityError(
            f"release artifact set does not match manifest; missing={missing}, extra={extra}"
        )
    for filename, expected in expected_by_name.items():
        if actual_by_name[filename] != expected:
            raise ReleaseIntegrityError(f"release artifact does not match manifest: {filename}")
    return ReleaseVerification(
        project=validated.project,
        version=validated.version,
        manifest_sha256=_release_manifest_sha256_validated(validated),
        artifact_count=len(validated.artifacts),
        total_bytes=sum(artifact.size_bytes for artifact in validated.artifacts),
        artifacts={artifact.filename: artifact.sha256 for artifact in validated.artifacts},
    )


def _requested_manifest_parent_identity(requested_parent: Path) -> os.stat_result:
    try:
        current_parent = requested_parent.resolve(strict=True)
        return current_parent.stat(follow_symlinks=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReleaseIntegrityError(f"unable to read release manifest: {exc}") from exc


def _read_release_manifest_bytes(path: Path) -> bytes:
    if "\x00" in os.fspath(path):
        raise ReleaseIntegrityError("release manifest path must not contain NUL")
    requested_parent = path.parent.absolute()
    try:
        parent = requested_parent.resolve(strict=True)
        parent_expected = parent.stat(follow_symlinks=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ReleaseIntegrityError(f"unable to read release manifest: {exc}") from exc
    if not stat.S_ISDIR(parent_expected.st_mode):
        raise ReleaseIntegrityError("release manifest parent must be a directory")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(parent, directory_flags)
    except OSError as exc:
        raise ReleaseIntegrityError(f"unable to read release manifest: {exc}") from exc
    descriptor: int | None = None
    try:
        parent_opened = os.fstat(parent_descriptor)
        requested_opened = _requested_manifest_parent_identity(requested_parent)
        if (
            _stat_identity(parent_opened) != _stat_identity(parent_expected)
            or _stat_identity(requested_opened) != _stat_identity(parent_opened)
        ):
            raise ReleaseIntegrityError("release manifest parent changed while opening")
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
            raise ReleaseIntegrityError(f"unable to read release manifest: {exc}") from exc
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
            raise ReleaseIntegrityError("release manifest must be a regular file")
        if expected.st_size > _MAX_MANIFEST_BYTES:
            raise ReleaseIntegrityError(f"release manifest exceeds {_MAX_MANIFEST_BYTES} bytes")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = (
                os.open(path.name, flags, dir_fd=parent_descriptor)
                if _OPEN_SUPPORTS_DIR_FD
                else os.open(parent / path.name, flags)
            )
        except OSError as exc:
            raise ReleaseIntegrityError(f"unable to read release manifest: {exc}") from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ReleaseIntegrityError("release manifest must be a regular file")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseIntegrityError("release manifest changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_MANIFEST_BYTES + 1)
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
        parent_current = _requested_manifest_parent_identity(requested_parent)
        if len(raw) > _MAX_MANIFEST_BYTES:
            raise ReleaseIntegrityError(f"release manifest exceeds {_MAX_MANIFEST_BYTES} bytes")
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or len(raw) != opened.st_size
        ):
            raise ReleaseIntegrityError("release manifest changed while reading")
        if _stat_identity(parent_after) != _stat_identity(parent_opened) or _stat_identity(
            parent_current
        ) != _stat_identity(parent_opened):
            raise ReleaseIntegrityError("release manifest parent changed while reading")
        return raw
    except OSError as exc:
        raise ReleaseIntegrityError(f"unable to read release manifest: {exc}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.close(parent_descriptor)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key!r}")
        result[key] = value
    return result


def load_release_manifest(path: Path) -> ReleaseManifest:
    """Load a bounded strict JSON release manifest."""
    if path.suffix.lower() != ".json":
        raise ReleaseIntegrityError("release manifest must use .json")
    raw = _read_release_manifest_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseIntegrityError("release manifest must be UTF-8") from exc
    try:
        data = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReleaseIntegrityError(f"invalid release manifest JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ReleaseIntegrityError("release manifest root must be an object")
    try:
        return ReleaseManifest.model_validate(data)
    except ValueError as exc:
        raise ReleaseIntegrityError(f"invalid release manifest: {exc}") from exc
