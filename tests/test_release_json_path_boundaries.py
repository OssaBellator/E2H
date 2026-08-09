from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.release import (
    ReleaseArtifact,
    ReleaseArtifactKind,
    ReleaseIntegrityError,
    ReleaseManifest,
    load_release_manifest,
    release_manifest_sha256,
)


def _manifest(metadata: dict[str, Any] | None = None) -> ReleaseManifest:
    return ReleaseManifest(
        project="pkg",
        version="1.0",
        artifacts=[
            ReleaseArtifact(
                filename="pkg-1.0-py3-none-any.whl",
                kind=ReleaseArtifactKind.WHEEL,
                size_bytes=1,
                sha256="a" * 64,
                package_name="pkg",
                package_version="1.0",
            ),
            ReleaseArtifact(
                filename="pkg-1.0.tar.gz",
                kind=ReleaseArtifactKind.SDIST,
                size_bytes=1,
                sha256="b" * 64,
                package_name="pkg",
                package_version="1.0",
            ),
        ],
        metadata={} if metadata is None else metadata,
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_release_manifest_rejects_json_coercible_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _manifest(metadata)


def test_release_digest_revalidates_mutated_json_coercion() -> None:
    manifest = _manifest()
    manifest.metadata["nested"] = (1, 2)

    with pytest.raises(ReleaseIntegrityError, match="invalid release manifest"):
        release_manifest_sha256(manifest)


def test_release_loader_rejects_nul_path_before_filesystem_access() -> None:
    with pytest.raises(ReleaseIntegrityError, match="path must not contain NUL"):
        load_release_manifest(Path("bad\x00manifest.json"))


def test_release_manifest_preserves_exact_nested_json() -> None:
    metadata = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}

    assert _manifest(metadata).metadata == metadata
