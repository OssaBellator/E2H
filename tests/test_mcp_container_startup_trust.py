from __future__ import annotations

from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.runner import ExecutionBackend


def test_explicit_container_replay_requires_attestation_before_capability_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes: list[str] = []

    def forbidden_probe() -> bool:
        probes.append("called")
        raise AssertionError("container capability must not be probed before operator attestation")

    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", forbidden_probe)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", forbidden_probe)

    with pytest.raises(MCPServiceError, match="explicit operator attestation"):
        E2HMCPService(
            MCPServerConfig(
                root=tmp_path,
                allow_replay=True,
                replay_backend=ExecutionBackend.CONTAINER,
                trusted_container_control_plane=False,
            )
        )

    assert probes == []


def test_auto_replay_does_not_require_container_attestation_at_startup(
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
            trusted_container_control_plane=False,
        )
    )

    assert service.config.replay_backend is ExecutionBackend.AUTO
    assert service.config.trusted_container_control_plane is False
