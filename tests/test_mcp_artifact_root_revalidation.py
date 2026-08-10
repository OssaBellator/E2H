from __future__ import annotations

from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError


def test_verify_artifact_rejects_parent_escape_between_resolution_and_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    parent = root / "artifacts"
    parent.mkdir()
    artifact = parent / "proof.bin"
    artifact.write_bytes(b"inside")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / artifact.name).write_bytes(b"outside")
    moved_parent = tmp_path / "original-artifacts"

    original = mcp_server._stable_file_sha256
    state = {"swapped": False}

    def swapping_hash(
        path: Path,
        *,
        max_bytes: int,
        root: Path,
    ) -> tuple[int, str]:
        if not state["swapped"]:
            state["swapped"] = True
            parent.rename(moved_parent)
            try:
                parent.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable: {exc}")
        return original(path, max_bytes=max_bytes, root=root)

    monkeypatch.setattr(mcp_server, "_stable_file_sha256", swapping_hash)
    service = E2HMCPService(MCPServerConfig(root=root))

    with pytest.raises(MCPServiceError, match="artifact parent escapes the configured MCP root"):
        service.verify_artifact("artifacts/proof.bin")

    assert state["swapped"] is True
