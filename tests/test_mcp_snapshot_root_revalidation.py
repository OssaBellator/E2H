from __future__ import annotations

from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.snapshot import SnapshotLimits, SnapshotManifest, create_snapshot


def _create_archive(root: Path, name: str, content: str) -> Path:
    source = root / f"{name}-source"
    source.mkdir()
    (source / "proof.txt").write_text(content, encoding="utf-8")
    archive = root / f"{name}.e2hsnap"
    create_snapshot(source, archive)
    return archive


def test_mcp_snapshot_rejects_parent_escape_between_resolution_and_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    parent = root / "archives"
    parent.mkdir()
    archive = _create_archive(parent, "artifact", "inside")

    outside = tmp_path / "outside"
    outside.mkdir()
    replacement = _create_archive(outside, "artifact", "outside")
    assert replacement.name == archive.name
    moved_parent = tmp_path / "original-archives"

    original_verify = mcp_server.verify_snapshot_with_archive_sha256
    state = {"swapped": False}

    def swapping_verify(
        path: Path,
        *,
        limits: SnapshotLimits | None = None,
        containment_root: Path | None = None,
    ) -> tuple[SnapshotManifest, str]:
        if not state["swapped"]:
            state["swapped"] = True
            parent.rename(moved_parent)
            try:
                parent.symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"symlinks unavailable: {exc}")
        return original_verify(
            path,
            limits=limits,
            containment_root=containment_root,
        )

    monkeypatch.setattr(mcp_server, "verify_snapshot_with_archive_sha256", swapping_verify)
    service = E2HMCPService(MCPServerConfig(root=root))

    with pytest.raises(
        MCPServiceError,
        match="snapshot archive parent escapes the configured root",
    ):
        service.verify_snapshot("archives/artifact.e2hsnap")

    assert state["swapped"] is True
