from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

import e2h.release_bundle as release_bundle
from e2h.release_bundle import ReleaseBundleError


def _bundle_source(tmp_path: Path) -> tuple[Path, dict[str, str], bytes, Path]:
    bundle = tmp_path / "bundle"
    dist = bundle / "dist"
    dist.mkdir(parents=True)

    for name in sorted(release_bundle._STATIC_CHECKSUM_PATHS):
        (bundle / name).write_bytes(f"fixture:{name}\n".encode())

    wheel = dist / "e2h-0.28.0-py3-none-any.whl"
    sdist = dist / "e2h-0.28.0.tar.gz"
    wheel.write_bytes(b"wheel-fixture\n")
    sdist.write_bytes(b"sdist-fixture\n")

    paths = [
        *sorted(release_bundle._STATIC_CHECKSUM_PATHS),
        f"dist/{wheel.name}",
        f"dist/{sdist.name}",
    ]
    checksums = {
        relative: hashlib.sha256(bundle.joinpath(*relative.split("/")).read_bytes()).hexdigest()
        for relative in paths
    }
    checksum_raw = "".join(
        f"{digest}  {relative}\n" for relative, digest in checksums.items()
    ).encode()
    (bundle / "release-checksums.txt").write_bytes(checksum_raw)
    return bundle, checksums, checksum_raw, wheel


def test_safe_bundle_staging_preserves_checksum_bound_bytes(tmp_path: Path) -> None:
    bundle, expected_checksums, expected_raw, wheel = _bundle_source(tmp_path)
    staging = tmp_path / "staging"
    staging.mkdir()

    checksums, checksum_raw = release_bundle._stage_verified_bundle_source(bundle, staging)

    assert checksums == expected_checksums
    assert checksum_raw == expected_raw
    assert (staging / "dist" / wheel.name).read_bytes() == b"wheel-fixture\n"
    assert (staging / "release-checksums.txt").read_bytes() == expected_raw


def test_bundle_staging_rejects_dist_directory_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _, _, wheel = _bundle_source(tmp_path)
    dist = bundle / "dist"
    moved = bundle / "original-dist"
    outside = tmp_path / "outside-dist"
    outside.mkdir()
    (outside / wheel.name).write_bytes(b"outside-wheel\n")
    (outside / "e2h-0.28.0.tar.gz").write_bytes(b"outside-sdist\n")
    staging = tmp_path / "staging"
    staging.mkdir()

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == wheel.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            dist.rename(moved)
            dist.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseBundleError, match="release bundle dist changed"):
        release_bundle._stage_verified_bundle_source(bundle, staging)

    assert swapped is True


def test_bundle_staging_rejects_root_directory_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _, _, _ = _bundle_source(tmp_path)
    moved = tmp_path / "original-bundle"
    outside = tmp_path / "outside-bundle"
    (outside / "dist").mkdir(parents=True)
    staging = tmp_path / "staging"
    staging.mkdir()

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if (
            Path(path).name == "release-manifest.json"
            and kwargs.get("dir_fd") is not None
            and not swapped
        ):
            swapped = True
            bundle.rename(moved)
            bundle.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseBundleError, match=r"release bundle(?: dist)? changed"):
        release_bundle._stage_verified_bundle_source(bundle, staging)

    assert swapped is True
