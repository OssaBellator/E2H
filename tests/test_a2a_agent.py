from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from a2a.helpers import new_data_message, new_text_message
from a2a.types import Part, Role
from starlette.testclient import TestClient

from e2h.a2a_agent import (
    A2AAgentError,
    A2AServerConfig,
    E2HVerificationAgentExecutor,
    MemoryQueryCommand,
    ReplayCommand,
    StatusCommand,
    VerifyArtifactCommand,
    VerifySnapshotCommand,
    _normalized_public_url,
    build_agent_card,
    create_a2a_app,
    execute_verification_command,
    parse_verification_message,
)
from e2h.mcp_server import E2HMCPService, MCPServerConfig
from e2h.runner import ExecutionBackend
from e2h.snapshot import create_snapshot
from e2h.store import initialize_store


def _service(
    root: Path,
    *,
    store: Path | None = None,
    allow_replay: bool = False,
    expose_replay_output: bool = False,
) -> E2HMCPService:
    return E2HMCPService(
        MCPServerConfig(
            root=root,
            store=store,
            allow_replay=allow_replay,
            replay_backend=ExecutionBackend.LOCAL,
            expose_replay_output=expose_replay_output,
            max_memory_rows=10,
        )
    )


def _write_capsule(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "id": "a2a-replay",
                "goal": "Exercise A2A replay.",
                "success": {
                    "commands": [
                        {
                            "id": "hello",
                            "argv": [sys.executable, "-c", "print('a2a-ok')"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_normalizes_public_url() -> None:
    assert _normalized_public_url("https://example.com/") == "https://example.com"
    assert _normalized_public_url("https://example.com/e2h/") == "https://example.com/e2h"
    with pytest.raises(A2AAgentError, match="http or https"):
        _normalized_public_url("file:///tmp/e2h")
    with pytest.raises(A2AAgentError, match="query or fragment"):
        _normalized_public_url("https://example.com/?x=1")
    with pytest.raises(A2AAgentError, match="non-empty"):
        _normalized_public_url("")


def test_parses_structured_data_command() -> None:
    message = new_data_message(
        {"schema_version": "0.1", "operation": "status"},
        role=Role.ROLE_USER,
        media_type="application/json",
    )
    command = parse_verification_message(message, max_bytes=1024)
    assert isinstance(command, StatusCommand)


def test_parses_json_text_command() -> None:
    message = new_text_message(
        json.dumps(
            {
                "schema_version": "0.1",
                "operation": "verify_artifact",
                "artifact": "result.json",
            }
        ),
        role=Role.ROLE_USER,
        media_type="application/json",
    )
    command = parse_verification_message(message, max_bytes=4096)
    assert isinstance(command, VerifyArtifactCommand)
    assert command.artifact == "result.json"


def test_rejects_ambiguous_or_unsupported_message_parts() -> None:
    two_parts = new_text_message("{}", role=Role.ROLE_USER)
    two_parts.parts.append(Part(text="{}"))
    with pytest.raises(A2AAgentError, match="exactly one"):
        parse_verification_message(two_parts, max_bytes=1024)

    unsupported = new_text_message("{}", role=Role.ROLE_USER)
    unsupported.parts[0].ClearField("text")
    unsupported.parts[0].raw = b"{}"
    with pytest.raises(A2AAgentError, match="only JSON text or data"):
        parse_verification_message(unsupported, max_bytes=1024)


def test_rejects_invalid_request_payloads() -> None:
    with pytest.raises(A2AAgentError, match="positive"):
        parse_verification_message(new_text_message("{}"), max_bytes=0)
    with pytest.raises(A2AAgentError, match="valid JSON"):
        parse_verification_message(new_text_message("{"), max_bytes=1024)
    with pytest.raises(A2AAgentError, match="JSON object"):
        parse_verification_message(new_text_message("[]"), max_bytes=1024)
    with pytest.raises(A2AAgentError, match="validation failed"):
        parse_verification_message(
            new_text_message('{"schema_version":"0.1","operation":"unknown"}'),
            max_bytes=1024,
        )
    with pytest.raises(A2AAgentError, match="exceeds 4 bytes"):
        parse_verification_message(new_text_message('{"x":1}'), max_bytes=4)


def test_executes_status_and_artifact_commands(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.txt"
    artifact.write_text("proof", encoding="utf-8")
    service = _service(tmp_path)

    status = execute_verification_command(service, StatusCommand(operation="status"))
    assert status.ok is True
    assert status.operation == "status"
    assert status.result is not None
    assert status.result["replay_enabled"] is False
    assert len(status.response_sha256) == 64

    digest = hashlib.sha256(b"proof").hexdigest()
    verified = execute_verification_command(
        service,
        VerifyArtifactCommand(
            operation="verify_artifact",
            artifact="proof.txt",
            expected_sha256=digest,
        ),
    )
    assert verified.ok is True
    assert verified.result is not None
    assert verified.result["verified"] is True

    missing = execute_verification_command(
        service,
        VerifyArtifactCommand(operation="verify_artifact", artifact="missing.txt"),
    )
    assert missing.ok is False
    assert missing.error is not None
    assert str(tmp_path.resolve()) not in missing.error


def test_executes_memory_snapshot_and_replay_commands(tmp_path: Path) -> None:
    database = tmp_path / "memory.duckdb"
    initialize_store(database)
    source = tmp_path / "snapshot-source"
    source.mkdir()
    (source / "data.txt").write_text("data", encoding="utf-8")
    create_snapshot(source, tmp_path / "proof.e2hsnap")
    _write_capsule(tmp_path / "capsule.json")
    service = _service(
        tmp_path,
        store=Path("memory.duckdb"),
        allow_replay=True,
    )

    memory = execute_verification_command(
        service,
        MemoryQueryCommand(operation="memory_query", view="sources", limit=2),
    )
    assert memory.ok is True
    assert memory.result is not None
    assert memory.result["row_count"] == 0

    snapshot = execute_verification_command(
        service,
        VerifySnapshotCommand(operation="verify_snapshot", archive="proof.e2hsnap"),
    )
    assert snapshot.ok is True
    assert snapshot.result is not None
    assert snapshot.result["verified"] is True

    replay = execute_verification_command(
        service,
        ReplayCommand(operation="replay", capsule="capsule.json"),
    )
    assert replay.ok is True
    assert replay.result is not None
    assert replay.result["status"] == "passed"
    assert replay.result["output_exposed"] is False


def test_replay_disabled_is_application_error(tmp_path: Path) -> None:
    service = _service(tmp_path)
    response = execute_verification_command(
        service,
        ReplayCommand(operation="replay", capsule="capsule.json"),
    )
    assert response.ok is False
    assert response.error == "replay is disabled by the MCP server operator"


def test_agent_card_advertises_only_enabled_capabilities(tmp_path: Path) -> None:
    service = _service(tmp_path)
    card = build_agent_card(service, public_url="https://verify.example/e2h/")
    assert card.supported_interfaces[0].protocol_version == "1.0"
    assert card.supported_interfaces[0].url == "https://verify.example/e2h/a2a/jsonrpc"
    skill_ids = {skill.id for skill in card.skills}
    assert "e2h_verify_artifact" in skill_ids
    assert "e2h_verify_snapshot" in skill_ids
    assert "e2h_memory_query" not in skill_ids
    assert "e2h_replay" not in skill_ids

    database = tmp_path / "memory.duckdb"
    initialize_store(database)
    enabled = _service(
        tmp_path,
        store=Path("memory.duckdb"),
        allow_replay=True,
    )
    enabled_ids = {
        skill.id for skill in build_agent_card(enabled, public_url="http://127.0.0.1:41242").skills
    }
    assert "e2h_memory_query" in enabled_ids
    assert "e2h_replay" in enabled_ids


def test_executor_rejects_invalid_response_limits(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(A2AAgentError, match="request bytes"):
        E2HVerificationAgentExecutor(service, max_request_bytes=0)
    with pytest.raises(A2AAgentError, match="response bytes"):
        E2HVerificationAgentExecutor(service, max_response_bytes=0)


def test_agent_card_route_and_jsonrpc_send_message(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.txt"
    artifact.write_text("proof", encoding="utf-8")
    app = create_a2a_app(
        A2AServerConfig(
            verification=MCPServerConfig(root=tmp_path, max_memory_rows=5),
            public_url="http://testserver",
        )
    )
    with TestClient(app) as client:
        card_response = client.get("/.well-known/agent-card.json")
        assert card_response.status_code == 200
        card_payload = card_response.json()
        assert card_payload["name"] == "E2H Verification Agent"
        assert card_payload["supportedInterfaces"][0]["protocolVersion"] == "1.0"

        response = client.post(
            "/a2a/jsonrpc",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": "verify-1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "message-1",
                        "role": "ROLE_USER",
                        "parts": [
                            {
                                "data": {
                                    "schema_version": "0.1",
                                    "operation": "verify_artifact",
                                    "artifact": "proof.txt",
                                },
                                "mediaType": "application/json",
                            }
                        ],
                    }
                },
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == "verify-1"
        result = payload["result"]["message"]["parts"][0]["data"]
        assert result["operation"] == "verify_artifact"
        assert result["ok"] is True
        assert result["result"]["verified"] is True


def test_jsonrpc_returns_structured_application_error(tmp_path: Path) -> None:
    app = create_a2a_app(
        A2AServerConfig(
            verification=MCPServerConfig(root=tmp_path),
            public_url="http://testserver",
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/a2a/jsonrpc",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": "invalid-1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "message-invalid",
                        "role": "ROLE_USER",
                        "parts": [{"text": "not-json"}],
                    }
                },
            },
        )
        assert response.status_code == 200
        result = response.json()["result"]["message"]["parts"][0]["data"]
        assert result["operation"] == "invalid_request"
        assert result["ok"] is False
        assert "valid JSON" in result["error"]


def test_response_limit_returns_bounded_error(tmp_path: Path) -> None:
    app = create_a2a_app(
        A2AServerConfig(
            verification=MCPServerConfig(root=tmp_path),
            public_url="http://testserver",
            max_response_bytes=1,
        )
    )
    with TestClient(app) as client:
        response = client.post(
            "/a2a/jsonrpc",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": "limit-1",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "message-limit",
                        "role": "ROLE_USER",
                        "parts": [
                            {"data": {"schema_version": "0.1", "operation": "status"}}
                        ],
                    }
                },
            },
        )
        assert response.status_code == 200
        result = response.json()["result"]["message"]["parts"][0]["data"]
        assert result["ok"] is False
        assert "response limit" in result["error"]
