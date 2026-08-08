from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from e2h.release import (
    ReleaseArtifact,
    ReleaseIntegrityError,
    ReleaseManifest,
    load_release_manifest,
)


def _manifest(version: str = "0.28.0") -> ReleaseManifest:
    artifacts = [
        ReleaseArtifact(
            filename=f"e2h-{version}-py3-none-any.whl",
            kind="wheel",
            size_bytes=10,
            sha256="a" * 64,
            package_name="e2h",
            package_version=version,
        ),
        ReleaseArtifact(
            filename=f"e2h-{version}.tar.gz",
            kind="sdist",
            size_bytes=20,
            sha256="b" * 64,
            package_name="e2h",
            package_version=version,
        ),
    ]
    return ReleaseManifest(
        project="e2h",
        version=version,
        artifacts=sorted(artifacts, key=lambda artifact: artifact.filename),
    )


def _write_manifest(path: Path, version: str = "0.28.0") -> None:
    path.write_text(
        json.dumps(_manifest(version).model_dump(mode="json")),
        encoding="utf-8",
    )


def test_load_release_manifest_rejects_final_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    _write_manifest(target)
    link = tmp_path / "release-manifest.json"
    link.symlink_to(target)

    with pytest.raises(ReleaseIntegrityError, match="release manifest must be a regular file"):
        load_release_manifest(link)


def test_load_release_manifest_rejects_file_swap_to_outside_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "bundle"
    directory.mkdir()
    manifest = directory / "release-manifest.json"
    _write_manifest(manifest)
    outside = tmp_path / "outside.json"
    _write_manifest(outside, version="9.9.9")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == manifest.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            manifest.unlink()
            manifest.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseIntegrityError, match="release manifest"):
        load_release_manifest(manifest)

    assert swapped is True


def test_load_release_manifest_rejects_parent_directory_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "bundle"
    directory.mkdir()
    manifest = directory / "release-manifest.json"
    _write_manifest(manifest)
    moved = tmp_path / "original-bundle"
    outside = tmp_path / "outside-bundle"
    outside.mkdir()
    _write_manifest(outside / manifest.name, version="9.9.9")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == manifest.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            directory.rename(moved)
            directory.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseIntegrityError, match="release manifest parent changed"):
        load_release_manifest(manifest)

    assert swapped is True
