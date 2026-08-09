from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import e2h.release as release
from e2h.release import (
    ReleaseIntegrityError,
    ReleaseManifest,
    load_release_manifest,
    release_manifest_sha256,
)


def _manifest_payload(metadata: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "project": "e2h",
        "version": "1.0",
        "artifacts": [
            {
                "filename": "e2h-1.0-py3-none-any.whl",
                "kind": "wheel",
                "size_bytes": 1,
                "sha256": "0" * 64,
                "package_name": "e2h",
                "package_version": "1.0",
            },
            {
                "filename": "e2h-1.0.tar.gz",
                "kind": "sdist",
                "size_bytes": 1,
                "sha256": "1" * 64,
                "package_name": "e2h",
                "package_version": "1.0",
            },
        ],
        "metadata": metadata or {},
    }


def _aliased_manifest(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    original = tmp_path / "original"
    original.mkdir()
    manifest = original / "release.json"
    manifest.write_text(json.dumps(_manifest_payload()), encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "marker.txt").write_text("replacement\n", encoding="utf-8")
    alias = tmp_path / "visible-parent"
    alias.symlink_to(original, target_is_directory=True)
    return alias, original, replacement, alias / manifest.name


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

    monkeypatch.setattr(release.os, "fdopen", swapping_fdopen)
    return state


def _rewrite_on_read(
    source: Path,
    replacement_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[os.stat_result, dict[str, bool]]:
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

    monkeypatch.setattr(release.os, "fdopen", rewriting_fdopen)
    return before, state


@pytest.mark.skipif(
    not release._OPEN_SUPPORTS_DIR_FD or not release._STAT_SUPPORTS_DIR_FD,
    reason="descriptor-relative release manifest reads are unavailable",
)
def test_descriptor_manifest_read_rejects_parent_alias_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, manifest = _aliased_manifest(tmp_path)
    state = _retarget_on_read(alias, replacement, monkeypatch)

    with pytest.raises(ReleaseIntegrityError, match="manifest parent changed while reading"):
        load_release_manifest(manifest)

    assert state["swapped"] is True
    assert alias.resolve() == replacement
    assert (original / manifest.name).is_file()
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"


def test_fallback_manifest_read_rejects_parent_alias_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, manifest = _aliased_manifest(tmp_path)
    monkeypatch.setattr(release, "_OPEN_SUPPORTS_DIR_FD", False)
    monkeypatch.setattr(release, "_STAT_SUPPORTS_DIR_FD", False)
    state = _retarget_on_read(alias, replacement, monkeypatch)

    with pytest.raises(ReleaseIntegrityError, match="manifest parent changed while reading"):
        load_release_manifest(manifest)

    assert state["swapped"] is True
    assert alias.resolve() == replacement
    assert (original / manifest.name).is_file()
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"


def test_release_manifest_rejects_duplicate_top_level_key(tmp_path: Path) -> None:
    payload = json.dumps(_manifest_payload())
    duplicate = payload.replace('"project": "e2h"', '"project": "e2h", "project": "e2h"')
    path = tmp_path / "release.json"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(ReleaseIntegrityError, match="duplicate object key"):
        load_release_manifest(path)


def test_release_manifest_rejects_duplicate_nested_key(tmp_path: Path) -> None:
    payload = json.dumps(_manifest_payload({"tag": "second"}))
    duplicate = payload.replace('"tag": "second"', '"tag": "first", "tag": "second"')
    path = tmp_path / "release.json"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(ReleaseIntegrityError, match="duplicate object key"):
        load_release_manifest(path)


def test_release_manifest_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "release.json"
    original_text = json.dumps(_manifest_payload({"tag": "a"}))
    replacement_text = json.dumps(_manifest_payload({"tag": "b"}))
    assert len(original_text) == len(replacement_text)
    path.write_text(original_text, encoding="utf-8")
    before, state = _rewrite_on_read(path, replacement_text, monkeypatch)

    with pytest.raises(ReleaseIntegrityError, match="manifest changed while reading"):
        load_release_manifest(path)

    assert state["rewritten"] is True
    after = path.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns


def test_release_manifest_metadata_rejects_nested_non_string_key() -> None:
    payload = _manifest_payload()
    payload["metadata"] = {"nested": {1: "one"}}

    with pytest.raises(ValidationError, match="canonical JSON"):
        ReleaseManifest.model_validate(payload)


def test_release_manifest_sha_revalidates_mutated_metadata() -> None:
    manifest = ReleaseManifest.model_validate(_manifest_payload({"nested": {"one": 1}}))
    manifest.metadata["nested"] = {1: "one"}

    with pytest.raises(ReleaseIntegrityError, match="canonical JSON"):
        release_manifest_sha256(manifest)
