from __future__ import annotations

import json
from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
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


def test_mcp_rejects_explicit_container_replay(
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


def test_mcp_auto_rejects_sandbox_capsule_before_container_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: False)
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

    def unexpected_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("container replay reached runtime launch")

    monkeypatch.setattr(mcp_server, "run_capsule_isolated_container", unexpected_run)

    with pytest.raises(MCPServiceError, match="isolated container replay is unavailable"):
        service.replay(capsule.name, workspace=workspace.name)
