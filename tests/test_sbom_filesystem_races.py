from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from e2h.sbom import SbomCanonicalizationError, canonicalize_cyclonedx_sbom_file


def _payload(version: str = "0.28.0") -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"component": {"name": "e2h", "version": version}},
        "components": [],
        "dependencies": [],
    }


def _write(path: Path, version: str = "0.28.0") -> None:
    path.write_text(json.dumps(_payload(version)), encoding="utf-8")


def test_sbom_file_rejects_final_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.cdx.json"
    _write(target)
    link = tmp_path / "e2h-sbom.cdx.json"
    link.symlink_to(target)

    with pytest.raises(SbomCanonicalizationError, match="SBOM must be a regular file"):
        canonicalize_cyclonedx_sbom_file(link)


def test_sbom_file_rejects_swap_to_outside_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "bundle"
    directory.mkdir()
    source = directory / "e2h-sbom.cdx.json"
    _write(source)
    outside = tmp_path / "outside.cdx.json"
    _write(outside, version="9.9.9")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == source.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            source.unlink()
            source.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(SbomCanonicalizationError, match="SBOM"):
        canonicalize_cyclonedx_sbom_file(source)

    assert swapped is True


def test_sbom_file_rejects_parent_directory_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "bundle"
    directory.mkdir()
    source = directory / "e2h-sbom.cdx.json"
    _write(source)
    moved = tmp_path / "original-bundle"
    outside = tmp_path / "outside-bundle"
    outside.mkdir()
    _write(outside / source.name, version="9.9.9")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path).name == source.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            directory.rename(moved)
            directory.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(SbomCanonicalizationError, match="SBOM parent changed"):
        canonicalize_cyclonedx_sbom_file(source)

    assert swapped is True
