from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from e2h.sbom import (
    SbomCanonicalizationError,
    canonicalize_cyclonedx_sbom,
    canonicalize_cyclonedx_sbom_file,
)


def _sbom() -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "serialNumber": "urn:uuid:generated",
        "metadata": {
            "timestamp": "2026-08-10T00:30:00Z",
            "component": {"name": "e2h", "type": "application"},
        },
        "components": [],
        "dependencies": [],
    }


def test_sbom_rejects_nested_integer_mapping_keys() -> None:
    data = _sbom()
    data["metadata"]["component"]["properties"] = {1: "coerced key"}

    with pytest.raises(SbomCanonicalizationError, match="canonical JSON data"):
        canonicalize_cyclonedx_sbom(data)


def test_sbom_rejects_nested_tuple_values() -> None:
    data = _sbom()
    data["metadata"]["component"]["licenses"] = ("MIT",)

    with pytest.raises(SbomCanonicalizationError, match="canonical JSON data"):
        canonicalize_cyclonedx_sbom(data)


def test_sbom_file_rejects_nul_path_before_filesystem_access() -> None:
    with pytest.raises(SbomCanonicalizationError, match="path must not contain NUL"):
        canonicalize_cyclonedx_sbom_file(Path("bad\x00sbom.json"))


def test_sbom_preserves_valid_canonical_output() -> None:
    rendered = canonicalize_cyclonedx_sbom(_sbom())
    parsed = json.loads(rendered)

    assert "serialNumber" not in parsed
    assert "timestamp" not in parsed["metadata"]
    assert parsed["metadata"]["component"] == {"name": "e2h", "type": "application"}
    assert rendered.endswith("\n")
