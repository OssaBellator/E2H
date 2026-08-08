"""End-to-end verification for checksum-bound E2H release bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from e2h.release import (
    ReleaseManifest,
    ReleaseVerification,
    load_release_manifest,
    release_manifest_sha256,
    verify_release_artifacts,
)
from e2h.release_toolchain import (
    ReleaseToolchainVerification,
    release_toolchain_evidence_from_metadata,
    verify_release_toolchain_evidence,
)
from e2h.sbom import canonicalize_cyclonedx_sbom_file
from e2h.snapshot import SnapshotManifest, restore_snapshot, verify_snapshot

_MAX_CHECKSUM_BYTES = 64 * 1024
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_BUNDLE_FILE_BYTES = 512 * 1024 * 1024
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CHECKSUM_LINE_RE = re.compile(r"^(?P<digest>[0-9a-f]{64})  (?P<path>[^\r\n]+)$")
_STATIC_CHECKSUM_PATHS = {
    "e2h-sbom.cdx.json",
    "e2h-source.e2hsnap",
    "release-manifest.json",
    "release-verification.json",
    "release-toolchain-verification.json",
    "release-inspection.json",
    "release-source-inspection.json",
}
_REQUIRED_TOP_LEVEL = _STATIC_CHECKSUM_PATHS | {"release-checksums.txt", "dist"}
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_LISTDIR_SUPPORTS_FD = os.listdir in os.supports_fd


class ReleaseBundleError(ValueError):
    """Raised when a release bundle cannot be verified safely."""


class ReleaseBundleVerification(BaseModel):
    """Machine-readable proof that a complete release bundle verified."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    project: str
    version: str
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_tree_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_commit: str
    source_commit_verified: bool = False
    artifact_count: int = Field(ge=1)
    checksum_count: int = Field(ge=1)
    checksum_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    checksums: dict[str, str]
    verified: Literal[True] = True


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def _open_real_directory(path: Path, *, noun: str) -> tuple[int, os.stat_result]:
    try:
        expected = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to inspect {noun}: {exc}") from exc
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
        raise ReleaseBundleError(f"{noun} must be a real directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to open {noun}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise ReleaseBundleError(f"{noun} is no longer a directory")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseBundleError(f"{noun} changed while opening")
        return descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _open_child_directory(
    parent_descriptor: int,
    path: Path,
    expected: os.stat_result,
    *,
    noun: str,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        if _OPEN_SUPPORTS_DIR_FD:
            descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        else:
            descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to open {noun}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise ReleaseBundleError(f"{noun} is no longer a directory")
        if _stat_identity(opened) != _stat_identity(expected):
            raise ReleaseBundleError(f"{noun} changed while opening")
        return descriptor, opened
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def _directory_must_be_stable(
    path: Path,
    descriptor: int,
    opened: os.stat_result,
    *,
    noun: str,
) -> None:
    try:
        after = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to restat {noun}: {exc}") from exc
    if _stat_identity(after) != _stat_identity(opened) or _stat_identity(current) != _stat_identity(
        opened
    ):
        raise ReleaseBundleError(f"{noun} changed during verification")


def _list_directory(
    path: Path,
    descriptor: int,
    opened: os.stat_result,
    *,
    noun: str,
) -> list[str]:
    try:
        names = sorted(os.listdir(descriptor)) if _LISTDIR_SUPPORTS_FD else sorted(os.listdir(path))
    except OSError as exc:
        raise ReleaseBundleError(f"unable to inspect {noun}: {exc}") from exc
    _directory_must_be_stable(path, descriptor, opened, noun=noun)
    return names


def _stat_directory_entry(
    directory_descriptor: int,
    path: Path,
    *,
    noun: str,
) -> os.stat_result:
    try:
        if _STAT_SUPPORTS_DIR_FD:
            return os.stat(
                path.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        return path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to stat {noun}: {exc}") from exc


def _read_regular_bytes(
    path: Path,
    *,
    limit: int,
    noun: str,
    directory_descriptor: int | None = None,
    expected: os.stat_result | None = None,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        if directory_descriptor is not None and _OPEN_SUPPORTS_DIR_FD:
            descriptor = os.open(path.name, flags, dir_fd=directory_descriptor)
        else:
            descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to open {noun}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseBundleError(f"{noun} must be a regular file")
        if expected is not None and _stat_identity(before) != _stat_identity(expected):
            raise ReleaseBundleError(f"{noun} changed while opening")
        if before.st_size <= 0:
            raise ReleaseBundleError(f"{noun} must not be empty")
        if before.st_size > limit:
            raise ReleaseBundleError(f"{noun} exceeds {limit} bytes")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after) or len(data) != before.st_size:
            raise ReleaseBundleError(f"{noun} changed while reading")
        if directory_descriptor is not None:
            current = _stat_directory_entry(directory_descriptor, path, noun=noun)
            if _stat_identity(current) != _stat_identity(before):
                raise ReleaseBundleError(f"{noun} changed while reading")
        return data
    except OSError as exc:
        raise ReleaseBundleError(f"unable to read {noun}: {exc}") from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _copy_verified_file(
    source: Path,
    destination: Path,
    *,
    expected_digest: str,
    noun: str,
    directory_descriptor: int | None = None,
    expected: os.stat_result | None = None,
) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        if directory_descriptor is not None and _OPEN_SUPPORTS_DIR_FD:
            descriptor = os.open(source.name, flags, dir_fd=directory_descriptor)
        else:
            descriptor = os.open(source, flags)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to open {noun}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseBundleError(f"{noun} must be a regular file")
        if expected is not None and _stat_identity(before) != _stat_identity(expected):
            raise ReleaseBundleError(f"{noun} changed while opening")
        if before.st_size <= 0:
            raise ReleaseBundleError(f"{noun} must not be empty")
        if before.st_size > _MAX_BUNDLE_FILE_BYTES:
            raise ReleaseBundleError(f"{noun} exceeds {_MAX_BUNDLE_FILE_BYTES} bytes")
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        observed = 0
        try:
            output = destination.open("xb")
        except OSError as exc:
            raise ReleaseBundleError(f"unable to stage {noun}: {exc}") from exc
        with output, os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                observed += len(chunk)
                if observed > _MAX_BUNDLE_FILE_BYTES:
                    raise ReleaseBundleError(f"{noun} exceeds {_MAX_BUNDLE_FILE_BYTES} bytes")
                output.write(chunk)
                digest.update(chunk)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after) or observed != before.st_size:
            raise ReleaseBundleError(f"{noun} changed while hashing")
        if directory_descriptor is not None:
            current = _stat_directory_entry(directory_descriptor, source, noun=noun)
            if _stat_identity(current) != _stat_identity(before):
                raise ReleaseBundleError(f"{noun} changed while hashing")
        if digest.hexdigest() != expected_digest:
            raise ReleaseBundleError(f"release checksum mismatch: {source.name}")
    except OSError as exc:
        raise ReleaseBundleError(f"unable to hash {noun}: {exc}") from exc
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _safe_checksum_path(value: str) -> str:
    if "\\" in value:
        raise ReleaseBundleError(f"unsafe checksum path: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseBundleError(f"unsafe checksum path: {value}")
    return value


def _parse_checksums(
    path: Path,
    *,
    directory_descriptor: int | None = None,
    expected: os.stat_result | None = None,
) -> tuple[dict[str, str], bytes]:
    raw = _read_regular_bytes(
        path,
        limit=_MAX_CHECKSUM_BYTES,
        noun="release checksum manifest",
        directory_descriptor=directory_descriptor,
        expected=expected,
    )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseBundleError("release checksum manifest must be UTF-8") from exc
    checksums: dict[str, str] = {}
    lines = text.splitlines()
    if not lines:
        raise ReleaseBundleError("release checksum manifest is empty")
    if len(lines) > 64:
        raise ReleaseBundleError("release checksum manifest has too many entries")
    for line in lines:
        match = _CHECKSUM_LINE_RE.fullmatch(line)
        if match is None:
            raise ReleaseBundleError("invalid release checksum line")
        relative = _safe_checksum_path(match.group("path"))
        if relative in checksums:
            raise ReleaseBundleError(f"duplicate release checksum path: {relative}")
        checksums[relative] = match.group("digest")
    return checksums, raw


def _validate_checksum_shape(checksums: dict[str, str]) -> None:
    paths = set(checksums)
    if not paths >= _STATIC_CHECKSUM_PATHS:
        missing = sorted(_STATIC_CHECKSUM_PATHS - paths)
        raise ReleaseBundleError(f"release checksum manifest is missing evidence: {missing}")
    distribution_paths = paths - _STATIC_CHECKSUM_PATHS
    if len(distribution_paths) != 2:
        raise ReleaseBundleError("release checksum manifest must contain exactly two distributions")
    if sum(path.startswith("dist/") and path.endswith(".whl") for path in distribution_paths) != 1:
        raise ReleaseBundleError("release checksum manifest must contain exactly one wheel")
    if (
        sum(path.startswith("dist/") and path.endswith(".tar.gz") for path in distribution_paths)
        != 1
    ):
        raise ReleaseBundleError("release checksum manifest must contain exactly one sdist")


def _load_json_object(path: Path, *, noun: str) -> dict[str, Any]:
    raw = _read_regular_bytes(path, limit=_MAX_JSON_BYTES, noun=noun)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseBundleError(f"{noun} must be UTF-8") from exc
    try:
        value = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReleaseBundleError(f"invalid {noun}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseBundleError(f"{noun} root must be an object")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _expected_release_inspection(manifest: ReleaseManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "project": manifest.project,
        "version": manifest.version,
        "manifest_sha256": release_manifest_sha256(manifest),
        "artifact_count": len(manifest.artifacts),
        "total_bytes": sum(artifact.size_bytes for artifact in manifest.artifacts),
        "metadata": manifest.metadata,
        "artifacts": [
            {
                "filename": artifact.filename,
                "kind": artifact.kind.value,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            }
            for artifact in manifest.artifacts
        ],
    }


def _verify_bundle_layout(directory: Path) -> None:
    try:
        if directory.is_symlink() or not directory.is_dir():
            raise ReleaseBundleError("release bundle must be a real directory")
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to inspect release bundle: {exc}") from exc
    names = {entry.name for entry in entries}
    if names != _REQUIRED_TOP_LEVEL:
        missing = sorted(_REQUIRED_TOP_LEVEL - names)
        extra = sorted(names - _REQUIRED_TOP_LEVEL)
        raise ReleaseBundleError(
            f"release bundle layout does not match; missing={missing}, extra={extra}"
        )
    for entry in entries:
        if entry.name == "dist":
            if entry.is_symlink() or not entry.is_dir():
                raise ReleaseBundleError("release bundle dist must be a real directory")
        elif entry.is_symlink() or not entry.is_file():
            raise ReleaseBundleError(f"release bundle entry must be a regular file: {entry.name}")


def _verify_distribution_layout(directory: Path, checksums: dict[str, str]) -> None:
    expected = {
        PurePosixPath(relative).name for relative in checksums if relative.startswith("dist/")
    }
    dist = directory / "dist"
    try:
        entries = sorted(dist.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to inspect release dist directory: {exc}") from exc
    actual = {entry.name for entry in entries}
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReleaseBundleError(
            f"release dist layout does not match checksums; missing={missing}, extra={extra}"
        )
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise ReleaseBundleError(f"release distribution must be a regular file: {entry.name}")


def _open_verified_bundle_layout(
    directory: Path,
) -> tuple[int, os.stat_result, int, os.stat_result]:
    root_descriptor, root_info = _open_real_directory(directory, noun="release bundle")
    dist_descriptor: int | None = None
    try:
        names = _list_directory(
            directory,
            root_descriptor,
            root_info,
            noun="release bundle",
        )
        actual = set(names)
        if actual != _REQUIRED_TOP_LEVEL:
            missing = sorted(_REQUIRED_TOP_LEVEL - actual)
            extra = sorted(actual - _REQUIRED_TOP_LEVEL)
            raise ReleaseBundleError(
                f"release bundle layout does not match; missing={missing}, extra={extra}"
            )
        dist_path = directory / "dist"
        dist_expected: os.stat_result | None = None
        for name in names:
            path = directory / name
            entry = _stat_directory_entry(
                root_descriptor,
                path,
                noun=f"release bundle entry {name}",
            )
            if name == "dist":
                if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
                    raise ReleaseBundleError("release bundle dist must be a real directory")
                dist_expected = entry
            elif stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
                raise ReleaseBundleError(f"release bundle entry must be a regular file: {name}")
        assert dist_expected is not None
        dist_descriptor, dist_info = _open_child_directory(
            root_descriptor,
            dist_path,
            dist_expected,
            noun="release bundle dist",
        )
        _directory_must_be_stable(
            directory,
            root_descriptor,
            root_info,
            noun="release bundle",
        )
        return root_descriptor, root_info, dist_descriptor, dist_info
    except Exception:
        if dist_descriptor is not None:
            with suppress(OSError):
                os.close(dist_descriptor)
        with suppress(OSError):
            os.close(root_descriptor)
        raise


def _verify_distribution_descriptor(
    dist: Path,
    descriptor: int,
    opened: os.stat_result,
    checksums: dict[str, str],
) -> None:
    expected = {
        PurePosixPath(relative).name for relative in checksums if relative.startswith("dist/")
    }
    names = _list_directory(
        dist,
        descriptor,
        opened,
        noun="release bundle dist",
    )
    actual = set(names)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReleaseBundleError(
            f"release dist layout does not match checksums; missing={missing}, extra={extra}"
        )
    for name in names:
        path = dist / name
        entry = _stat_directory_entry(
            descriptor,
            path,
            noun=f"release distribution {name}",
        )
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISREG(entry.st_mode):
            raise ReleaseBundleError(f"release distribution must be a regular file: {name}")


def _stage_verified_bundle(
    directory: Path,
    staging: Path,
    checksums: dict[str, str],
    checksum_raw: bytes,
    *,
    root_descriptor: int | None = None,
    dist_descriptor: int | None = None,
) -> None:
    for relative, expected_digest in checksums.items():
        relative_path = PurePosixPath(relative)
        source = directory / Path(*relative_path.parts)
        destination = staging / Path(*relative_path.parts)
        directory_descriptor: int | None = None
        expected: os.stat_result | None = None
        if relative_path.parts[0] == "dist" and dist_descriptor is not None:
            directory_descriptor = dist_descriptor
            expected = _stat_directory_entry(
                dist_descriptor,
                source,
                noun=f"release bundle file {relative}",
            )
        elif len(relative_path.parts) == 1 and root_descriptor is not None:
            directory_descriptor = root_descriptor
            expected = _stat_directory_entry(
                root_descriptor,
                source,
                noun=f"release bundle file {relative}",
            )
        _copy_verified_file(
            source,
            destination,
            expected_digest=expected_digest,
            noun=f"release bundle file {relative}",
            directory_descriptor=directory_descriptor,
            expected=expected,
        )
    try:
        (staging / "release-checksums.txt").write_bytes(checksum_raw)
    except OSError as exc:
        raise ReleaseBundleError(f"unable to stage release checksum manifest: {exc}") from exc


def _stage_verified_bundle_source(
    directory: Path,
    staging: Path,
) -> tuple[dict[str, str], bytes]:
    root_descriptor, root_info, dist_descriptor, dist_info = _open_verified_bundle_layout(directory)
    dist = directory / "dist"
    try:
        checksum_path = directory / "release-checksums.txt"
        checksum_expected = _stat_directory_entry(
            root_descriptor,
            checksum_path,
            noun="release checksum manifest",
        )
        checksums, checksum_raw = _parse_checksums(
            checksum_path,
            directory_descriptor=root_descriptor,
            expected=checksum_expected,
        )
        _validate_checksum_shape(checksums)
        _verify_distribution_descriptor(dist, dist_descriptor, dist_info, checksums)
        _stage_verified_bundle(
            directory,
            staging,
            checksums,
            checksum_raw,
            root_descriptor=root_descriptor,
            dist_descriptor=dist_descriptor,
        )
        _directory_must_be_stable(
            dist,
            dist_descriptor,
            dist_info,
            noun="release bundle dist",
        )
        _directory_must_be_stable(
            directory,
            root_descriptor,
            root_info,
            noun="release bundle",
        )
        return checksums, checksum_raw
    finally:
        with suppress(OSError):
            os.close(dist_descriptor)
        with suppress(OSError):
            os.close(root_descriptor)


def _validate_stored_reports(
    directory: Path,
    manifest: ReleaseManifest,
    release_verification: ReleaseVerification,
    source_manifest: SnapshotManifest,
    producer_toolchain: ReleaseToolchainVerification,
) -> None:
    try:
        stored_release = ReleaseVerification.model_validate(
            _load_json_object(
                directory / "release-verification.json",
                noun="release verification report",
            )
        )
    except ValueError as exc:
        raise ReleaseBundleError(f"invalid release verification report: {exc}") from exc
    if stored_release != release_verification:
        raise ReleaseBundleError("release verification report does not match bundle contents")

    release_inspection = _load_json_object(
        directory / "release-inspection.json",
        noun="release inspection report",
    )
    if release_inspection != _expected_release_inspection(manifest):
        raise ReleaseBundleError("release inspection report does not match release manifest")

    try:
        stored_source = SnapshotManifest.model_validate(
            _load_json_object(
                directory / "release-source-inspection.json",
                noun="release source inspection report",
            )
        )
    except ValueError as exc:
        raise ReleaseBundleError(f"invalid release source inspection report: {exc}") from exc
    if stored_source != source_manifest:
        raise ReleaseBundleError("release source inspection report does not match source snapshot")

    try:
        stored_toolchain = ReleaseToolchainVerification.model_validate(
            _load_json_object(
                directory / "release-toolchain-verification.json",
                noun="release toolchain verification report",
            )
        )
    except ValueError as exc:
        raise ReleaseBundleError(f"invalid release toolchain verification report: {exc}") from exc
    if stored_toolchain != producer_toolchain:
        raise ReleaseBundleError(
            "release toolchain verification report does not match restored source"
        )


def _verify_staged_bundle(
    directory: Path,
    checksums: dict[str, str],
    *,
    expected_source_commit: str | None,
) -> tuple[ReleaseManifest, bool]:
    try:
        manifest = load_release_manifest(directory / "release-manifest.json")
    except ValueError as exc:
        raise ReleaseBundleError(f"invalid release manifest: {exc}") from exc
    expected_paths = _STATIC_CHECKSUM_PATHS | {
        f"dist/{artifact.filename}" for artifact in manifest.artifacts
    }
    if set(checksums) != expected_paths:
        missing = sorted(expected_paths - set(checksums))
        extra = sorted(set(checksums) - expected_paths)
        raise ReleaseBundleError(
            f"release checksum set does not match manifest; missing={missing}, extra={extra}"
        )

    try:
        release_verification = verify_release_artifacts(manifest, directory / "dist")
    except ValueError as exc:
        raise ReleaseBundleError(f"release distribution verification failed: {exc}") from exc

    sbom_path = directory / "e2h-sbom.cdx.json"
    try:
        canonical_sbom = canonicalize_cyclonedx_sbom_file(sbom_path).encode("utf-8")
    except ValueError as exc:
        raise ReleaseBundleError(f"release SBOM verification failed: {exc}") from exc
    if _read_regular_bytes(sbom_path, limit=8 * 1024 * 1024, noun="release SBOM") != canonical_sbom:
        raise ReleaseBundleError("release SBOM is not canonical")
    sbom = json.loads(canonical_sbom)
    component = sbom["metadata"]["component"]
    if component.get("name") != manifest.project or component.get("version") != manifest.version:
        raise ReleaseBundleError("release SBOM component does not match release manifest")

    source_path = directory / "e2h-source.e2hsnap"
    try:
        source_manifest = verify_snapshot(source_path)
        evidence = release_toolchain_evidence_from_metadata(manifest.metadata)
    except ValueError as exc:
        raise ReleaseBundleError(f"release source verification failed: {exc}") from exc
    if evidence.source_tree_sha256 is None:
        raise ReleaseBundleError("release toolchain evidence has no source_tree_sha256")
    if source_manifest.snapshot_id != evidence.source_tree_sha256:
        raise ReleaseBundleError("release source snapshot does not match source_tree_sha256")

    restored = directory.parent / "restored-source"
    try:
        restore_snapshot(source_path, restored)
        producer_toolchain = verify_release_toolchain_evidence(
            manifest.metadata,
            restored,
            expected_source_commit=evidence.source_commit,
        )
        consumer_toolchain = verify_release_toolchain_evidence(
            manifest.metadata,
            restored,
            expected_source_commit=expected_source_commit,
        )
    except ValueError as exc:
        raise ReleaseBundleError(f"release toolchain verification failed: {exc}") from exc

    _validate_stored_reports(
        directory,
        manifest,
        release_verification,
        source_manifest,
        producer_toolchain,
    )
    return manifest, consumer_toolchain.source_commit_verified


def verify_release_bundle(
    directory: Path,
    *,
    expected_source_commit: str | None = None,
) -> ReleaseBundleVerification:
    """Verify checksums and all semantic relationships in a complete release bundle."""
    with tempfile.TemporaryDirectory(prefix="e2h-release-bundle-") as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir()
        checksums, checksum_raw = _stage_verified_bundle_source(directory, staging)
        manifest, source_commit_verified = _verify_staged_bundle(
            staging,
            checksums,
            expected_source_commit=expected_source_commit,
        )
        evidence = release_toolchain_evidence_from_metadata(manifest.metadata)
        assert evidence.source_tree_sha256 is not None

    return ReleaseBundleVerification(
        project=manifest.project,
        version=manifest.version,
        manifest_sha256=release_manifest_sha256(manifest),
        source_tree_sha256=evidence.source_tree_sha256,
        source_commit=evidence.source_commit,
        source_commit_verified=source_commit_verified,
        artifact_count=len(manifest.artifacts),
        checksum_count=len(checksums),
        checksum_manifest_sha256=hashlib.sha256(checksum_raw).hexdigest(),
        checksums=dict(sorted(checksums.items())),
    )
