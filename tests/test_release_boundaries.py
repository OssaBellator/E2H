from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from e2h.release import (
    ReleaseArtifactKind,
    ReleaseIntegrityError,
    ReleaseManifest,
    seal_release_artifacts,
    verify_release_artifacts,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _ManifestSubclass(ReleaseManifest):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def _metadata() -> bytes:
    return b"Metadata-Version: 2.4\nName: e2h\nVersion: 0.28.0\n\n"


def _release_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "dist"
    directory.mkdir()
    wheel = directory / "e2h-0.28.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("e2h-0.28.0.dist-info/METADATA", _metadata())

    sdist = directory / "e2h-0.28.0.tar.gz"
    payload = _metadata()
    with tarfile.open(sdist, mode="w:gz") as archive:
        info = tarfile.TarInfo("e2h-0.28.0/PKG-INFO")
        info.size = len(payload)
        info.mtime = 1_704_067_200
        archive.addfile(info, io.BytesIO(payload))
    return directory


def test_verifier_rejects_manifest_subclass_and_lookalike(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    subclassed = _ManifestSubclass.model_validate(manifest.model_dump(mode="json"))
    lookalike = _Lookalike.model_validate(manifest.model_dump(mode="json"))

    with pytest.raises(
        ReleaseIntegrityError,
        match="expected ReleaseManifest, got _ManifestSubclass",
    ):
        verify_release_artifacts(subclassed, directory)

    with pytest.raises(
        ReleaseIntegrityError,
        match="expected ReleaseManifest, got _Lookalike",
    ):
        verify_release_artifacts(cast(Any, lookalike), directory)

    with pytest.raises(
        ReleaseIntegrityError,
        match="expected ReleaseManifest, got object",
    ):
        verify_release_artifacts(cast(Any, object()), directory)


def test_verifier_revalidates_unsorted_artifacts(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    manifest.artifacts.reverse()

    with pytest.raises(ReleaseIntegrityError, match="release artifacts must be sorted"):
        verify_release_artifacts(manifest, directory)


def test_verifier_revalidates_duplicate_artifacts(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    manifest.artifacts.append(manifest.artifacts[-1])

    with pytest.raises(ReleaseIntegrityError, match="filenames must be unique"):
        verify_release_artifacts(manifest, directory)


def test_verifier_revalidates_incomplete_artifact_kinds(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    manifest.artifacts[-1].kind = ReleaseArtifactKind.WHEEL

    with pytest.raises(ReleaseIntegrityError, match="exactly a wheel and an sdist kind"):
        verify_release_artifacts(manifest, directory)


def test_verifier_revalidates_artifact_package_identity(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    manifest.artifacts[0].package_version = "9.9.9"

    with pytest.raises(ReleaseIntegrityError, match="package version must match"):
        verify_release_artifacts(manifest, directory)


def test_verifier_preserves_canonical_invalid_metadata(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    manifest.metadata = {"invalid": {"set-value"}}

    with pytest.raises(ReleaseIntegrityError, match="canonical JSON"):
        verify_release_artifacts(manifest, directory)


def test_verifier_normalizes_warning_prone_raw_artifact_assignment(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    manifest.artifacts = [artifact.model_dump(mode="json") for artifact in manifest.artifacts]

    verification = verify_release_artifacts(manifest, directory)

    assert verification.verified is True
    assert verification.project == "e2h"
    assert verification.version == "0.28.0"
    assert verification.artifact_count == 2
