from __future__ import annotations

import json
from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.runner import ExecutionBackend


def _sandbox_capsule(path: Path, *, workspace_access: str = "read_only") -> None:
    path.write_text(
        json.dumps(
            {
                "id": "container-boundary",
                "goal": "Exercise the MCP container replay boundary.",
                "sandbox": {
                    "image": "python@sha256:" + "0" * 64,
                    "workspace_access": workspace_access,
                },
                "success": {
                    "commands": [
                        {"id": "check", "argv": ["python", "-V"]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_mcp_rejects_explicit_container_when_snapshot_host_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: False)

    with pytest.raises(MCPServiceError, match="isolated container replay is unavailable"):
        E2HMCPService(
            MCPServerConfig(
                root=tmp_path,
                allow_replay=True,
                replay_backend=ExecutionBackend.CONTAINER,
            )
        )


def test_mcp_rejects_writable_isolated_container_workspace_before_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: True)
    capsule = tmp_path / "capsule.json"
    _sandbox_capsule(capsule, workspace_access="read_write")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.CONTAINER,
        )
    )

    def unexpected_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("writable isolated workspace reached container launch")

    monkeypatch.setattr(mcp_server, "run_capsule_isolated_container", unexpected_run)

    with pytest.raises(MCPServiceError, match="workspace_access='read_only'"):
        service.replay(capsule.name, workspace=workspace.name)
