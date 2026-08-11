from __future__ import annotations

from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.runner import ExecutionBackend


def test_replay_disabled_does_not_require_replay_host_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: False)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: False)

    service = E2HMCPService(MCPServerConfig(root=tmp_path, allow_replay=False))

    assert service.status().replay_enabled is False


def test_explicit_local_replay_requires_handle_bound_local_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: False)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: False)

    with pytest.raises(MCPServiceError, match="MCP local replay is unavailable"):
        E2HMCPService(
            MCPServerConfig(
                root=tmp_path,
                allow_replay=True,
                replay_backend=ExecutionBackend.LOCAL,
            )
        )


def test_explicit_container_replay_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: False)

    with pytest.raises(MCPServiceError, match="MCP isolated container replay is unavailable"):
        E2HMCPService(
            MCPServerConfig(
                root=tmp_path,
                allow_replay=True,
                replay_backend=ExecutionBackend.CONTAINER,
            )
        )


def test_auto_replay_requires_supported_local_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: False)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: False)

    with pytest.raises(MCPServiceError, match="supports neither"):
        E2HMCPService(
            MCPServerConfig(
                root=tmp_path,
                allow_replay=True,
                replay_backend=ExecutionBackend.AUTO,
            )
        )


def test_auto_replay_constructs_with_handle_bound_local_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: False)

    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.AUTO,
        )
    )

    assert service.status().replay_enabled is True
