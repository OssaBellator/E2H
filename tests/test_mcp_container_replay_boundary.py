from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.runner import ExecutionBackend


def _sandbox_capsule(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "id": "container-boundary",
                "goal": "Exercise the MCP container replay boundary.",
                "sandbox": {"image": "python@sha256:" + "0" * 64},
                "success": {
                    "commands": [
                        {"id": "check", "argv": ["python", "-V"]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_mcp_rejects_explicit_container_replay_configuration(tmp_path: Path) -> None:
    with pytest.raises(MCPServiceError, match="container replay is unavailable"):
        E2HMCPService(
            MCPServerConfig(
                root=tmp_path,
                allow_replay=True,
                replay_backend=ExecutionBackend.CONTAINER,
            )
        )


def test_mcp_auto_rejects_sandbox_capsule_before_container_launch(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule.json"
    _sandbox_capsule(capsule)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.AUTO,
        )
    )

    with pytest.raises(MCPServiceError, match="container replay is unavailable"):
        service.replay(capsule.name, workspace=workspace.name)
