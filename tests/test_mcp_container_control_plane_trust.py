from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import e2h.a2a_agent as a2a_agent
import e2h.mcp_server as mcp_server
from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.runner import ExecutionBackend


def _write_capsule(path: Path, *, sandbox: bool) -> None:
    payload: dict[str, object] = {
        "schema_version": "0.1",
        "id": "container-control-plane-trust",
        "goal": "Exercise the operator trust boundary.",
        "success": {
            "commands": [
                {
                    "id": "check",
                    "argv": ["python", "-c", "print('ok')"],
                }
            ]
        },
    }
    if sandbox:
        payload["sandbox"] = {
            "image": "python@sha256:" + "0" * 64,
            "workspace_access": "read_only",
        }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_container_control_plane_attestation_is_default_deny_and_visible_in_status(
    tmp_path: Path,
) -> None:
    service = E2HMCPService(MCPServerConfig(root=tmp_path))

    assert service.config.trusted_container_control_plane is False
    assert service.status().container_control_plane_attested is False


def test_explicit_container_replay_requires_trust_before_replay_probe_or_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: True)
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.CONTAINER,
        )
    )
    capsule = tmp_path / "capsule.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_capsule(capsule, sandbox=True)

    def unexpected_probe() -> bool:
        raise AssertionError("container capability probe ran before trust gate")

    def unexpected_launch(*args: object, **kwargs: object) -> object:
        raise AssertionError("container replay launched before trust gate")

    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", unexpected_probe)
    monkeypatch.setattr(mcp_server, "run_capsule_isolated_container", unexpected_launch)

    with pytest.raises(MCPServiceError, match="explicit operator attestation"):
        service.replay(capsule.name, workspace=workspace.name)


def test_auto_container_selection_requires_trust_after_capsule_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: True)
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.AUTO,
        )
    )
    capsule = tmp_path / "capsule.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_capsule(capsule, sandbox=True)

    def unexpected_probe() -> bool:
        raise AssertionError("container capability probe ran before trust gate")

    def unexpected_launch(*args: object, **kwargs: object) -> object:
        raise AssertionError("container replay launched before trust gate")

    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", unexpected_probe)
    monkeypatch.setattr(mcp_server, "run_capsule_isolated_container", unexpected_launch)

    with pytest.raises(MCPServiceError, match="explicit operator attestation"):
        service.replay(capsule.name, workspace=workspace.name)


def test_local_replay_does_not_require_container_control_plane_trust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: True)
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.AUTO,
        )
    )
    capsule = tmp_path / "capsule.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_capsule(capsule, sandbox=False)

    @contextmanager
    def fake_bound_directory(path: Path) -> Iterator[int]:
        assert path == workspace.resolve()
        yield 123

    def local_reached(*args: object, **kwargs: object) -> object:
        raise RuntimeError("local replay reached")

    monkeypatch.setattr(mcp_server, "bound_absolute_directory", fake_bound_directory)
    monkeypatch.setattr(mcp_server, "run_capsule_bound_local", local_reached)

    with pytest.raises(RuntimeError, match="local replay reached"):
        service.replay(capsule.name, workspace=workspace.name)


def test_trusted_container_control_plane_advances_to_container_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: True)
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.CONTAINER,
            trusted_container_control_plane=True,
        )
    )
    capsule = tmp_path / "capsule.json"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_capsule(capsule, sandbox=True)

    def container_reached(*args: object, **kwargs: object) -> object:
        raise RuntimeError("container replay reached")

    monkeypatch.setattr(mcp_server, "run_capsule_isolated_container", container_reached)

    with pytest.raises(RuntimeError, match="container replay reached"):
        service.replay(capsule.name, workspace=workspace.name)


def test_mcp_and_a2a_cli_trust_attestation_is_opt_in() -> None:
    for parser in (mcp_server._parser(), a2a_agent._parser()):
        assert parser.parse_args([]).trusted_container_control_plane is False
        assert (
            parser.parse_args(["--trusted-container-control-plane"]).trusted_container_control_plane
            is True
        )


def test_mcp_main_wires_trust_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, MCPServerConfig] = {}

    class FakeServer:
        def run(self) -> None:
            return None

    def fake_create(config: MCPServerConfig) -> FakeServer:
        captured["config"] = config
        return FakeServer()

    monkeypatch.setattr(mcp_server, "create_mcp_server", fake_create)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e2h-mcp",
            "--root",
            str(tmp_path),
            "--trusted-container-control-plane",
        ],
    )

    mcp_server.main()

    assert captured["config"].trusted_container_control_plane is True


def test_a2a_main_wires_trust_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, a2a_agent.A2AServerConfig] = {}

    def fake_create(config: a2a_agent.A2AServerConfig) -> object:
        captured["config"] = config
        return object()

    monkeypatch.setattr(a2a_agent, "create_a2a_app", fake_create)
    monkeypatch.setattr(a2a_agent.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e2h-a2a",
            "--root",
            str(tmp_path),
            "--trusted-container-control-plane",
        ],
    )

    a2a_agent.main()

    assert captured["config"].verification.trusted_container_control_plane is True
