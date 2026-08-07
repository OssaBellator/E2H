"""Canonicalize CycloneDX SBOM exports for deterministic release publication."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_MAX_SBOM_BYTES = 8 * 1024 * 1024
_SUPPORTED_SPEC_VERSION = "1.5"


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


def canonicalize_cyclonedx_sbom(data: dict[str, Any]) -> str:
    """Remove per-generation identity fields and emit canonical CycloneDX JSON."""
    if data.get("bomFormat") != "CycloneDX":
        raise SbomCanonicalizationError("SBOM bomFormat must be CycloneDX")
    if data.get("specVersion") != _SUPPORTED_SPEC_VERSION:
        raise SbomCanonicalizationError(
            f"SBOM specVersion must be {_SUPPORTED_SPEC_VERSION}"
        )
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
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise SbomCanonicalizationError(f"unable to read SBOM: {exc}") from exc
    if len(raw) > _MAX_SBOM_BYTES:
        raise SbomCanonicalizationError(f"SBOM exceeds {_MAX_SBOM_BYTES} bytes")
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
