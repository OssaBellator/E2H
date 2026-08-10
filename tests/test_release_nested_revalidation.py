from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.release import ReleaseArtifact, ReleaseArtifactKind, ReleaseManifest


def _artifact(filename: str, kind: ReleaseArtifactKind, digest: str) -> ReleaseArtifact:
    return ReleaseArtifact(
        filename=filename,
        kind=kind,
        size_bytes=1,
        sha256=digest * 64,
        package_name="pkg",
        package_version="1.0",
    )


def _manifest(wheel: ReleaseArtifact) -> ReleaseManifest:
    sdist = _artifact("pkg-1.0.tar.gz", ReleaseArtifactKind.SDIST, "b")
    return ReleaseManifest(project="pkg", version="1.0", artifacts=[wheel, sdist])


def test_release_manifest_revalidates_mutated_artifact_filename() -> None:
    wheel = _artifact("pkg-1.0-py3-none-any.whl", ReleaseArtifactKind.WHEEL, "a")
    wheel.filename = "bad/name.whl"

    with pytest.raises(ValidationError, match="filename must be a portable basename"):
        _manifest(wheel)


def test_release_manifest_revalidates_mutated_artifact_size() -> None:
    wheel = _artifact("pkg-1.0-py3-none-any.whl", ReleaseArtifactKind.WHEEL, "a")
    wheel.size_bytes = 0

    with pytest.raises(ValidationError) as exc_info:
        _manifest(wheel)

    assert exc_info.value.errors()[0]["loc"][-1] == "size_bytes"
