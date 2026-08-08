from __future__ import annotations

import warnings
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from e2h.release import (
    ReleaseArtifact,
    ReleaseIntegrityError,
    ReleaseManifest,
    release_manifest_sha256,
)


class _ManifestSubclass(ReleaseManifest):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def _manifest() -> ReleaseManifest:
    artifacts = [
        ReleaseArtifact(
            filename="e2h-0.28.0.tar.gz",
            kind="sdist",
            size_bytes=10,
            sha256="a" * 64,
            package_name="e2h",
            package_version="0.28.0",
        ),
        ReleaseArtifact(
            filename="e2h-0.28.0-py3-none-any.whl",
            kind="wheel",
            size_bytes=20,
            sha256="b" * 64,
            package_name="e2h",
            package_version="0.28.0",
        ),
    ]
    return ReleaseManifest(
        project="e2h",
        version="0.28.0",
        artifacts=sorted(artifacts, key=lambda artifact: artifact.filename),
        metadata={"channel": "test"},
    )


def test_release_manifest_digest_revalidates_mutated_manifest() -> None:
    manifest = _manifest()
    manifest.artifacts.reverse()

    with pytest.raises(ReleaseIntegrityError, match="release artifacts must be sorted"):
        release_manifest_sha256(manifest)


def test_release_manifest_digest_rejects_canonical_invalid_metadata() -> None:
    manifest = _manifest()
    manifest.metadata = {"invalid": {"set-value"}}

    with pytest.raises(ReleaseIntegrityError, match="invalid release manifest"):
        release_manifest_sha256(manifest)


def test_release_manifest_digest_rejects_subclasses_and_lookalikes() -> None:
    manifest = _manifest()
    subclassed = _ManifestSubclass.model_validate(manifest.model_dump(mode="python"))
    lookalike = _Lookalike.model_validate(manifest.model_dump(mode="python"))

    with pytest.raises(
        ReleaseIntegrityError,
        match="expected ReleaseManifest, got _ManifestSubclass",
    ):
        release_manifest_sha256(subclassed)

    with pytest.raises(
        ReleaseIntegrityError,
        match="expected ReleaseManifest, got _Lookalike",
    ):
        release_manifest_sha256(cast(Any, lookalike))


def test_release_manifest_digest_normalizes_raw_nested_assignments_without_warnings() -> None:
    manifest = _manifest()
    expected = release_manifest_sha256(manifest)
    manifest.artifacts = [artifact.model_dump(mode="python") for artifact in manifest.artifacts]

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        actual = release_manifest_sha256(manifest)

    assert actual == expected
