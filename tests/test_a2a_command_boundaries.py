from __future__ import annotations

from pathlib import Path

import pytest
from a2a.helpers import new_text_message

from e2h.a2a_agent import (
    A2AAgentError,
    StatusCommand,
    VerifyArtifactCommand,
    _response,
    execute_verification_command,
    parse_verification_message,
)
from e2h.mcp_server import E2HMCPService, MCPServerConfig


def test_text_command_rejects_duplicate_object_keys() -> None:
    message = new_text_message(
        '{"schema_version":"0.1","operation":"status","operation":"replay"}'
    )

    with pytest.raises(A2AAgentError, match="valid JSON"):
        parse_verification_message(message, max_bytes=1024)


def test_text_command_rejects_nonstandard_json_constants() -> None:
    message = new_text_message(
        '{"schema_version":"0.1","operation":"memory_query","view":"sources","limit":NaN}'
    )

    with pytest.raises(A2AAgentError, match="valid JSON"):
        parse_verification_message(message, max_bytes=1024)


def test_execute_revalidates_mutated_command(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.txt"
    artifact.write_text("proof", encoding="utf-8")
    service = E2HMCPService(MCPServerConfig(root=tmp_path))
    command = VerifyArtifactCommand(operation="verify_artifact", artifact="proof.txt")
    command.expected_sha256 = 1  # type: ignore[assignment]

    with pytest.raises(A2AAgentError, match="invalid verification command"):
        execute_verification_command(service, command)


def test_execute_rejects_command_subclasses(tmp_path: Path) -> None:
    class StatusSubclass(StatusCommand):
        pass

    service = E2HMCPService(MCPServerConfig(root=tmp_path))
    command = StatusSubclass(operation="status")

    with pytest.raises(A2AAgentError, match="invalid verification command type"):
        execute_verification_command(service, command)  # type: ignore[arg-type]


def test_response_digest_rejects_json_coercion() -> None:
    with pytest.raises(A2AAgentError, match="canonical JSON data"):
        _response("status", result={"nested": (1, 2)})  # type: ignore[dict-item]


def test_valid_status_command_still_executes(tmp_path: Path) -> None:
    service = E2HMCPService(MCPServerConfig(root=tmp_path))

    response = execute_verification_command(service, StatusCommand(operation="status"))

    assert response.ok is True
    assert response.result is not None
    assert response.result["replay_enabled"] is False
    assert len(response.response_sha256) == 64
