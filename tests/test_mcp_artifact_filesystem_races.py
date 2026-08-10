from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import MCPServiceError, _stable_file_sha256


def _rewrite_on_read(
    artifact: Path,
    replacement: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[os.stat_result, dict[str, bool]]:
    before = artifact.stat(follow_symlinks=False)
    original_fdopen = os.fdopen
    state = {"rewritten": False}

    def rewriting_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["rewritten"]:
            state["rewritten"] = True
            artifact.write_bytes(replacement)
            os.utime(
                artifact,
                ns=(before.st_atime_ns, before.st_mtime_ns),
                follow_symlinks=False,
            )
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(mcp_server.os, "fdopen", rewriting_fdopen)
    return before, state


def test_stable_file_sha256_hashes_unchanged_regular_file(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    content = b"verified artifact"
    artifact.write_bytes(content)

    size, digest = _stable_file_sha256(artifact, max_bytes=100)

    assert size == len(content)
    assert digest == hashlib.sha256(content).hexdigest()


def test_stable_file_sha256_rejects_final_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"target")
    artifact = tmp_path / "artifact.bin"
    artifact.symlink_to(target)

    with pytest.raises(MCPServiceError, match="artifact is not a regular file"):
        _stable_file_sha256(artifact, max_bytes=100)


@pytest.mark.skipif(
    not mcp_server._ARTIFACT_DIR_FD_SUPPORTED,
    reason="descriptor-relative artifact reads are unavailable",
)
def test_descriptor_artifact_hash_rejects_same_size_replacement_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.bin"
    original = b'{"v":"one"}'
    replacement_bytes = b'{"v":"two"}'
    assert len(original) == len(replacement_bytes)
    artifact.write_bytes(original)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(replacement_bytes)
    moved = tmp_path / "original.bin"
    original_open = os.open
    state = {"swapped": False}

    def swapping_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if (
            not state["swapped"]
            and kwargs.get("dir_fd") is not None
            and str(target) == artifact.name
        ):
            state["swapped"] = True
            artifact.rename(moved)
            replacement.rename(artifact)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(mcp_server.os, "open", swapping_open)

    with pytest.raises(MCPServiceError, match="artifact changed while opening"):
        _stable_file_sha256(artifact, max_bytes=100)

    assert state["swapped"] is True


@pytest.mark.skipif(
    not mcp_server._ARTIFACT_DIR_FD_SUPPORTED,
    reason="descriptor-relative artifact reads are unavailable",
)
def test_descriptor_artifact_hash_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.bin"
    original = b'{"v":"one"}'
    replacement = b'{"v":"two"}'
    artifact.write_bytes(original)
    before, state = _rewrite_on_read(artifact, replacement, monkeypatch)

    with pytest.raises(MCPServiceError, match="artifact changed while it was being verified"):
        _stable_file_sha256(artifact, max_bytes=100)

    assert state["rewritten"] is True
    after = artifact.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns


def test_fallback_artifact_hash_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact.bin"
    original = b'{"v":"one"}'
    replacement = b'{"v":"two"}'
    artifact.write_bytes(original)
    monkeypatch.setattr(mcp_server, "_ARTIFACT_DIR_FD_SUPPORTED", False)
    before, state = _rewrite_on_read(artifact, replacement, monkeypatch)

    with pytest.raises(MCPServiceError, match="artifact changed while it was being verified"):
        _stable_file_sha256(artifact, max_bytes=100)

    assert state["rewritten"] is True
    after = artifact.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns


def test_artifact_hash_rejects_parent_replacement_while_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "artifacts"
    parent.mkdir()
    artifact = parent / "artifact.bin"
    artifact.write_bytes(b"inside")
    replacement_parent = tmp_path / "replacement"
    replacement_parent.mkdir()
    (replacement_parent / artifact.name).write_bytes(b"outside")
    moved_parent = tmp_path / "original"
    original_open = os.open
    state = {"swapped": False}

    def swapping_open(target: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if (
            not state["swapped"]
            and kwargs.get("dir_fd") is None
            and Path(target) == parent
        ):
            state["swapped"] = True
            parent.rename(moved_parent)
            replacement_parent.rename(parent)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(mcp_server.os, "open", swapping_open)

    with pytest.raises(MCPServiceError, match="artifact parent changed while opening"):
        _stable_file_sha256(artifact, max_bytes=100)

    assert state["swapped"] is True
