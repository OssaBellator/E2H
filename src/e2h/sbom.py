"""Canonicalize CycloneDX SBOM exports for deterministic release publication."""

from __future__ import annotations

import json
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any

_MAX_SBOM_BYTES = 8 * 1024 * 1024
_SUPPORTED_SPEC_VERSION = "1.5"
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd


class SbomCanonicalizationError(ValueError):
    """Raised when a CycloneDX release SBOM cannot be safely canonicalized."""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _canonical_json(value: Any) -> str:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
    except (TypeError, ValueError) as exc:
        raise SbomCanonicalizationError("SBOM must contain canonical JSON data") from exc


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode)


def _requested_parent_identity(requested_parent: Path) -> os.stat_result:
    try:
        current_parent = requested_parent.resolve(strict=True)
        return current_parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise SbomCanonicalizationError(f"unable to read SBOM: {exc}") from exc


def _read_sbom_bytes(source: Path) -> bytes:
    requested_parent = source.parent.absolute()
    try:
        parent = requested_parent.resolve(strict=True)
        parent_expected = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise SbomCanonicalizationError(f"unable to read SBOM: {exc}") from exc
    if not stat.S_ISDIR(parent_expected.st_mode):
        raise SbomCanonicalizationError("SBOM parent must be a directory")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(parent, directory_flags)
    except OSError as exc:
        raise SbomCanonicalizationError(f"unable to read SBOM: {exc}") from exc
    descriptor: int | None = None
    try:
        parent_opened = os.fstat(parent_descriptor)
        requested_opened = _requested_parent_identity(requested_parent)
        if (
            _stat_identity(parent_opened) != _stat_identity(parent_expected)
            or _stat_identity(requested_opened) != _stat_identity(parent_opened)
        ):
            raise SbomCanonicalizationError("SBOM parent changed while opening")
        try:
            expected = (
                os.stat(
                    source.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if _STAT_SUPPORTS_DIR_FD
                else (parent / source.name).stat(follow_symlinks=False)
            )
        except OSError as exc:
            raise SbomCanonicalizationError(f"unable to read SBOM: {exc}") from exc
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
            raise SbomCanonicalizationError("SBOM must be a regular file")
        if expected.st_size > _MAX_SBOM_BYTES:
            raise SbomCanonicalizationError(f"SBOM exceeds {_MAX_SBOM_BYTES} bytes")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = (
                os.open(source.name, flags, dir_fd=parent_descriptor)
                if _OPEN_SUPPORTS_DIR_FD
                else os.open(parent / source.name, flags)
            )
        except OSError as exc:
            raise SbomCanonicalizationError(f"unable to read SBOM: {exc}") from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SbomCanonicalizationError("SBOM must be a regular file")
        if _stat_identity(opened) != _stat_identity(expected):
            raise SbomCanonicalizationError("SBOM changed while opening")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_SBOM_BYTES + 1)
        after = os.fstat(descriptor)
        current = (
            os.stat(
                source.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if _STAT_SUPPORTS_DIR_FD
            else (parent / source.name).stat(follow_symlinks=False)
        )
        parent_after = os.fstat(parent_descriptor)
        parent_current = _requested_parent_identity(requested_parent)
        if len(raw) > _MAX_SBOM_BYTES:
            raise SbomCanonicalizationError(f"SBOM exceeds {_MAX_SBOM_BYTES} bytes")
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or len(raw) != opened.st_size
        ):
            raise SbomCanonicalizationError("SBOM changed while reading")
        if _stat_identity(parent_after) != _stat_identity(parent_opened) or _stat_identity(
            parent_current
        ) != _stat_identity(parent_opened):
            raise SbomCanonicalizationError("SBOM parent changed while reading")
        return raw
    except OSError as exc:
        raise SbomCanonicalizationError(f"unable to read SBOM: {exc}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.close(parent_descriptor)


def canonicalize_cyclonedx_sbom(data: dict[str, Any]) -> str:
    """Remove per-generation identity fields and emit canonical CycloneDX JSON."""
    if data.get("bomFormat") != "CycloneDX":
        raise SbomCanonicalizationError("SBOM bomFormat must be CycloneDX")
    if data.get("specVersion") != _SUPPORTED_SPEC_VERSION:
        raise SbomCanonicalizationError(f"SBOM specVersion must be {_SUPPORTED_SPEC_VERSION}")
    if data.get("version") != 1:
        raise SbomCanonicalizationError("SBOM version must be 1")

    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        raise SbomCanonicalizationError("SBOM metadata must be an object")
    component = metadata.get("component")
    if not isinstance(component, dict) or component.get("name") != "e2h":
        raise SbomCanonicalizationError("SBOM metadata component must identify e2h")

    components = data.get("components")
    if not isinstance(components, list):
        raise SbomCanonicalizationError("SBOM components must be an array")
    for item in components:
        if not isinstance(item, dict):
            raise SbomCanonicalizationError("SBOM components must contain objects")

    dependencies = data.get("dependencies")
    if not isinstance(dependencies, list):
        raise SbomCanonicalizationError("SBOM dependencies must be an array")
    for item in dependencies:
        if not isinstance(item, dict):
            raise SbomCanonicalizationError("SBOM dependencies must contain objects")

    normalized = dict(data)
    normalized.pop("serialNumber", None)
    normalized_metadata = dict(metadata)
    normalized_metadata.pop("timestamp", None)
    normalized["metadata"] = normalized_metadata
    return _canonical_json(normalized)


def canonicalize_cyclonedx_sbom_file(source: Path) -> str:
    """Load a bounded UTF-8 CycloneDX JSON document and canonicalize it."""
    raw = _read_sbom_bytes(source)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SbomCanonicalizationError("SBOM must be UTF-8") from exc
    try:
        parsed = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SbomCanonicalizationError(f"invalid SBOM JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SbomCanonicalizationError("SBOM root must be an object")
    return canonicalize_cyclonedx_sbom(parsed)
