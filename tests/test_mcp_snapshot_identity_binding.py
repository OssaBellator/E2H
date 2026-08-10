from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.snapshot import create_snapshot


def _create_archive(root: Path, name: str, content: str) -> Path:
    source = root / f"{name}-source"
    source.mkdir()
    (source / "proof.txt").write_text(content, encoding="utf-8")
    archive = root / f"{name}.e2hsnap"
    create_snapshot(source, archive)
    return archive


def test_verify_snapshot_binds_manifest_and_archive_digest_to_same_bytes(tmp_path: Path) -> None:
    archive = _create_archive(tmp_path, "artifact", "proof")
    expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    service = E2HMCPService(MCPServerConfig(root=tmp_path))

    result = service.verify_snapshot(archive.name)

    assert result.archive_sha256 == expected_digest
    assert result.verified is True


def test_verify_snapshot_rejects_replacement_between_verification_and_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _create_archive(tmp_path, "artifact", "original")
    replacement = _create_archive(tmp_path, "replacement", "changed")
    moved = tmp_path / "original.e2hsnap"
    original_hash = mcp_server._archive_sha256_handle
    state = {"swapped": False}

    def swapping_hash(handle: object) -> str:
        if not state["swapped"]:
            state["swapped"] = True
            archive.rename(moved)
            replacement.rename(archive)
        return original_hash(handle)  # type: ignore[arg-type]

    monkeypatch.setattr(mcp_server, "_archive_sha256_handle", swapping_hash)
    service = E2HMCPService(MCPServerConfig(root=tmp_path))

    with pytest.raises(MCPServiceError, match="snapshot archive changed while reading"):
        service.verify_snapshot(archive.name)

    assert state["swapped"] is True
    assert archive.read_bytes() != moved.read_bytes()
