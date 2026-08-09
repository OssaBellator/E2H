from __future__ import annotations

from pathlib import Path

import pytest

from e2h.sbom import SbomCanonicalizationError, canonicalize_cyclonedx_sbom_file


def _base_json(metadata: str) -> str:
    return (
        '{"bomFormat":"CycloneDX","specVersion":"1.5","version":1,'
        f'"metadata":{metadata},"components":[],"dependencies":[]}}'
    )


def test_sbom_file_rejects_top_level_duplicate_key(tmp_path: Path) -> None:
    source = tmp_path / "sbom.json"
    source.write_text(
        '{"bomFormat":"CycloneDX","bomFormat":"CycloneDX",'
        '"specVersion":"1.5","version":1,'
        '"metadata":{"component":{"name":"e2h"}},'
        '"components":[],"dependencies":[]}',
        encoding="utf-8",
    )

    with pytest.raises(SbomCanonicalizationError, match="duplicate object key"):
        canonicalize_cyclonedx_sbom_file(source)


def test_sbom_file_rejects_nested_duplicate_key(tmp_path: Path) -> None:
    source = tmp_path / "sbom.json"
    source.write_text(
        _base_json('{"component":{"name":"e2h","name":"e2h"}}'),
        encoding="utf-8",
    )

    with pytest.raises(SbomCanonicalizationError, match="duplicate object key"):
        canonicalize_cyclonedx_sbom_file(source)
