from __future__ import annotations

from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.runner import ExecutionBackend


def test_mcp_replay_rejects_oversized_capsule_before_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: False)
    capsule = tmp_path / "capsule.json"
    capsule.write_bytes(b" " * 17)
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.AUTO,
            max_artifact_bytes=16,
        )
    )

    with pytest.raises(MCPServiceError, match="capsule exceeds 16 bytes"):
        service.replay("capsule.json")
