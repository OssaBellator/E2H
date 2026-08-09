from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.document as document
from e2h.document import load_mapping_document


def _aliased_document(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    original = tmp_path / "original"
    original.mkdir()
    source = original / "config.json"
    source.write_text('{"value":1}\n', encoding="utf-8")
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

    monkeypatch.setattr(document.os, "fdopen", swapping_fdopen)
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

    monkeypatch.setattr(document.os, "fdopen", rewriting_fdopen)
    return before, state


@pytest.mark.skipif(
    not document._OPEN_SUPPORTS_DIR_FD or not document._STAT_SUPPORTS_DIR_FD,
    reason="descriptor-relative document reads are unavailable",
)
def test_descriptor_document_read_rejects_parent_alias_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, source = _aliased_document(tmp_path)
    state = _retarget_on_read(alias, replacement, monkeypatch)

    with pytest.raises(ValueError, match="document parent changed while reading"):
        load_mapping_document(source, noun="document")

    assert state["swapped"] is True
    assert alias.resolve() == replacement
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert (original / source.name).read_text(encoding="utf-8") == '{"value":1}\n'


def test_fallback_document_read_rejects_parent_alias_retarget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias, original, replacement, source = _aliased_document(tmp_path)
    monkeypatch.setattr(document, "_OPEN_SUPPORTS_DIR_FD", False)
    monkeypatch.setattr(document, "_STAT_SUPPORTS_DIR_FD", False)
    state = _retarget_on_read(alias, replacement, monkeypatch)

    with pytest.raises(ValueError, match="document parent changed while reading"):
        load_mapping_document(source, noun="document")

    assert state["swapped"] is True
    assert alias.resolve() == replacement
    assert (replacement / "marker.txt").read_text(encoding="utf-8") == "replacement\n"
    assert (original / source.name).read_text(encoding="utf-8") == '{"value":1}\n'


@pytest.mark.skipif(
    not document._OPEN_SUPPORTS_DIR_FD or not document._STAT_SUPPORTS_DIR_FD,
    reason="descriptor-relative document reads are unavailable",
)
def test_descriptor_document_read_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "config.json"
    original_text = '{"value":1}\n'
    replacement_text = '{"value":2}\n'
    source.write_text(original_text, encoding="utf-8")
    before, state = _rewrite_on_read(source, replacement_text, monkeypatch)

    with pytest.raises(ValueError, match="document changed while reading"):
        load_mapping_document(source, noun="document")

    assert state["rewritten"] is True
    after = source.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns


def test_fallback_document_read_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "config.json"
    original_text = '{"value":1}\n'
    replacement_text = '{"value":2}\n'
    source.write_text(original_text, encoding="utf-8")
    monkeypatch.setattr(document, "_OPEN_SUPPORTS_DIR_FD", False)
    monkeypatch.setattr(document, "_STAT_SUPPORTS_DIR_FD", False)
    before, state = _rewrite_on_read(source, replacement_text, monkeypatch)

    with pytest.raises(ValueError, match="document changed while reading"):
        load_mapping_document(source, noun="document")

    assert state["rewritten"] is True
    after = source.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns
