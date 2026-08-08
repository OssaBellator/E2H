from __future__ import annotations

import io
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest

from e2h.release import ReleaseIntegrityError, seal_release_artifacts


def _metadata(version: str = "0.28.0") -> bytes:
    return f"Metadata-Version: 2.4\nName: e2h\nVersion: {version}\n\n".encode()


def _write_wheel(directory: Path, *, version: str = "0.28.0") -> Path:
    path = directory / f"e2h-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"e2h-{version}.dist-info/METADATA", _metadata(version))
    return path


def _write_sdist(directory: Path, *, version: str = "0.28.0") -> Path:
    path = directory / f"e2h-{version}.tar.gz"
    payload = _metadata(version)
    with tarfile.open(path, mode="w:gz") as archive:
        info = tarfile.TarInfo(f"e2h-{version}/PKG-INFO")
        info.size = len(payload)
        info.mtime = 1_704_067_200
        archive.addfile(info, io.BytesIO(payload))
    return path


def _release_dir(tmp_path: Path) -> tuple[Path, Path]:
    directory = tmp_path / "dist"
    directory.mkdir()
    wheel = _write_wheel(directory)
    _write_sdist(directory)
    return directory, wheel


def test_seal_rejects_artifact_swapped_to_outside_symlink_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, wheel = _release_dir(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside = _write_wheel(outside_dir, version="9.9.9")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == wheel.name and not swapped:
            swapped = True
            wheel.unlink()
            wheel.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseIntegrityError, match="release artifact"):
        seal_release_artifacts(directory)

    assert swapped is True


def test_seal_rejects_release_directory_replaced_after_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory, wheel = _release_dir(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _write_wheel(outside)
    _write_sdist(outside)
    moved = tmp_path / "original-dist"

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == wheel.name and not swapped:
            swapped = True
            directory.rename(moved)
            directory.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(ReleaseIntegrityError, match=r"release (?:directory|artifact) changed"):
        seal_release_artifacts(directory)

    assert swapped is True
