from __future__ import annotations

from pathlib import Path

from e2h.a2a_agent import _parser, _public_error, build_agent_card
from e2h.mcp_server import E2HMCPService, MCPServerConfig
from e2h.runner import ExecutionBackend


def test_public_error_does_not_mangle_filesystem_root() -> None:
    message = "unable to inspect /tmp/evidence"

    assert _public_error(message, root="/") == message


def test_a2a_help_describes_remote_container_fail_closed_boundary() -> None:
    help_text = _parser().format_help()

    assert "container replay is currently unavailable over A2A" in help_text
    assert "command-executing local replay" in help_text


def test_replay_skill_advertises_local_handle_bound_execution(tmp_path: Path) -> None:
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.LOCAL,
        )
    )

    card = build_agent_card(service, public_url="https://verify.example")
    replay = next(skill for skill in card.skills if skill.id == "e2h_replay")

    assert "handle-bound local replay" in replay.description
