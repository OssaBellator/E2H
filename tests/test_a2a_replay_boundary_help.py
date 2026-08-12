from __future__ import annotations

from pathlib import Path

import pytest

from e2h.a2a_agent import _parser, _public_error, build_agent_card
from e2h.bound_runner import handle_bound_local_replay_supported
from e2h.mcp_server import E2HMCPService, MCPServerConfig
from e2h.runner import ExecutionBackend


def test_public_error_does_not_mangle_filesystem_root() -> None:
    message = "unable to inspect /tmp/evidence"

    assert _public_error(message, root="/") == message


def test_a2a_help_describes_local_and_isolated_container_replay() -> None:
    help_text = " ".join(_parser().format_help().split())

    assert "read-only isolated workspace copy" in help_text
    assert "command-executing replay" in help_text
    assert "currently unavailable" not in help_text


@pytest.mark.skipif(
    not handle_bound_local_replay_supported(),
    reason="local replay skill check requires supported handle-bound local host",
)
def test_replay_skill_advertises_local_and_isolated_execution(tmp_path: Path) -> None:
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
    assert "read-only isolated container workspace" in replay.description
