from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

import e2h.sbom as sbom
from e2h.sbom import SbomCanonicalizationError, canonicalize_cyclonedx_sbom_file


def _aliased_sbom(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    original = tmp_path / "original"
    original.mkdir()
    source = original / "sbom.json"
    source.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "version": 1,
                "metadata": {"component": {"name": "e2h"}},
                "components": [],
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    alias = tmp_path / "visible-parent"
    alias.symlink_to(original, target_is_directory=True)
    return alias, original, replacement, alias / source.name


def _retarget_on_read(
    alias: Path,
    replacement: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, bool]:
    original_fdopen = os.fdopen
    state = {"swapped": False}

    def swapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["swapped"]:
            state["swapped"] = True
            alias.unlink()
            alias.symlink_to(replacement, target_is_directory=True)
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(sbom.os, "fdopen", swapping_fdopen)
    return state


@pytest.mark.skipif(
    not sbom._OPEN_SUPPORTS_DIR_FD or not sbom._STAT_SUPPORTS_DIR_FD,
    reason="descriptor-relative SBOM reads are unavailable",
)
def test_descriptor_sbom_read_rejects_parent_alias_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, source = _aliased_sbom(tmp_path)
    state = _retarget_on_read(alias, replacement, monkeypatch)

    with pytest.raises(SbomCanonicalizationError, match="parent changed while reading"):
        canonicalize_cyclonedx_sbom_file(source)

    assert state["swapped"] is True
    assert alias.resolve() == replacement
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert (original / source.name).is_file()


def test_fallback_sbom_read_rejects_parent_alias_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, source = _aliased_sbom(tmp_path)
    monkeypatch.setattr(sbom, "_OPEN_SUPPORTS_DIR_FD", False)
    monkeypatch.setattr(sbom, "_STAT_SUPPORTS_DIR_FD", False)
    state = _retarget_on_read(alias, replacement, monkeypatch)

    with pytest.raises(SbomCanonicalizationError, match="parent changed while reading"):
        canonicalize_cyclonedx_sbom_file(source)

    assert state["swapped"] is True
    assert alias.resolve() == replacement
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert (original / source.name).is_file()


def test_sbom_read_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "sbom.json"
    original_text = json.dumps(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {"component": {"name": "e2h"}},
            "components": [],
            "dependencies": [],
            "note": "a",
        }
    )
    replacement_text = original_text.replace('"note": "a"', '"note": "b"')
    assert len(replacement_text) == len(original_text)
    source.write_text(original_text, encoding="utf-8")
    before = source.stat(follow_symlinks=False)
    original_fdopen = os.fdopen
    state = {"rewritten": False}

    def rewriting_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["rewritten"]:
            state["rewritten"] = True
            source.write_text(replacement_text, encoding="utf-8")
            os.utime(
                source,
                ns=(before.st_atime_ns, before.st_mtime_ns),
                follow_symlinks=False,
            )
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(sbom.os, "fdopen", rewriting_fdopen)

    with pytest.raises(SbomCanonicalizationError, match="SBOM changed while reading"):
        canonicalize_cyclonedx_sbom_file(source)

    assert state["rewritten"] is True
    after = source.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns
