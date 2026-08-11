from __future__ import annotations

from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.runner import ExecutionBackend


def test_replay_disabled_does_not_require_handle_bound_host_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: False)

    service = E2HMCPService(MCPServerConfig(root=tmp_path, allow_replay=False))

    assert service.status().replay_enabled is False


@pytest.mark.parametrize("backend", [ExecutionBackend.LOCAL, ExecutionBackend.AUTO])
def test_remote_replay_rejects_host_without_handle_bound_local_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: ExecutionBackend,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: False)

    with pytest.raises(MCPServiceError, match="MCP local replay is unavailable"):
        E2HMCPService(
            MCPServerConfig(
                root=tmp_path,
                allow_replay=True,
                replay_backend=backend,
            )
        )


def test_supported_local_replay_configuration_still_constructs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)

    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.LOCAL,
        )
    )

    assert service.status().replay_enabled is True


def test_explicit_container_error_takes_precedence_over_host_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: False)

    with pytest.raises(MCPServiceError, match="MCP container replay is unavailable"):
        E2HMCPService(
            MCPServerConfig(
                root=tmp_path,
                allow_replay=True,
                replay_backend=ExecutionBackend.CONTAINER,
            )
        )
