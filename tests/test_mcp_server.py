from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest
from mcp import Client

import e2h.mcp_server as mcp_server
from e2h.bound_runner import handle_bound_local_replay_supported
from e2h.mcp_server import (
    E2HMCPService,
    MCPServerConfig,
    MCPServiceError,
    create_mcp_server,
)
from e2h.runner import ExecutionBackend
from e2h.snapshot import create_snapshot
from e2h.store import initialize_store
from e2h.store_models import QueryView


def _write_capsule(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "id": "mcp-replay",
                "goal": "Exercise MCP replay.",
                "success": {
                    "commands": [
                        {
                            "id": "hello",
                            "argv": [sys.executable, "-c", "print('mcp-ok')"],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_status_does_not_expose_absolute_root(tmp_path: Path) -> None:
    service = E2HMCPService(MCPServerConfig(root=tmp_path))
    status = service.status()
    assert status.root_label == tmp_path.name
    assert status.memory_enabled is False
    assert status.replay_enabled is False
    assert status.replay_output_exposed is False
    assert str(tmp_path.resolve()) not in status.model_dump_json()


def test_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(MCPServiceError, match="root is not a directory"):
        E2HMCPService(MCPServerConfig(root=tmp_path / "missing"))


def test_rejects_invalid_limits(tmp_path: Path) -> None:
    with pytest.raises(MCPServiceError, match="max_artifact_bytes"):
        E2HMCPService(MCPServerConfig(root=tmp_path, max_artifact_bytes=0))
    with pytest.raises(MCPServiceError, match="max_memory_rows"):
        E2HMCPService(MCPServerConfig(root=tmp_path, max_memory_rows=0))


def test_rejects_store_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.duckdb"
    with pytest.raises(MCPServiceError, match="memory store must be inside"):
        E2HMCPService(MCPServerConfig(root=tmp_path, store=outside))


def test_memory_query_requires_configured_existing_store(tmp_path: Path) -> None:
    service = E2HMCPService(MCPServerConfig(root=tmp_path))
    with pytest.raises(MCPServiceError, match="not configured"):
        service.memory_query(QueryView.RUNS)

    configured = E2HMCPService(MCPServerConfig(root=tmp_path, store=Path(".e2h/memory.duckdb")))
    with pytest.raises(MCPServiceError, match="does not exist"):
        configured.memory_query(QueryView.RUNS)


def test_memory_query_is_bounded_and_digest_stable(tmp_path: Path) -> None:
    database = tmp_path / ".e2h" / "memory.duckdb"
    initialize_store(database)
    service = E2HMCPService(
        MCPServerConfig(root=tmp_path, store=Path(".e2h/memory.duckdb"), max_memory_rows=3)
    )
    first = service.memory_query(QueryView.SOURCES, limit=3)
    second = service.memory_query(QueryView.SOURCES, limit=3)
    assert first.rows == []
    assert first.row_count == 0
    assert first.store_source_count == 0
    assert first.memory_sha256 == second.memory_sha256
    assert len(first.memory_sha256) == 64
    with pytest.raises(MCPServiceError, match="configured max_memory_rows"):
        service.memory_query(QueryView.RUNS, limit=4)


def test_verify_artifact_returns_digest_and_expectation_result(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"verified artifact")
    digest = hashlib.sha256(b"verified artifact").hexdigest()
    service = E2HMCPService(MCPServerConfig(root=tmp_path))

    result = service.verify_artifact(
        "artifact.bin",
        expected_sha256=digest.upper(),
        min_bytes=1,
        max_bytes=100,
    )
    assert result.artifact == "artifact.bin"
    assert result.sha256 == digest
    assert result.digest_matches is True
    assert result.size_matches is True
    assert result.verified is True

    mismatch = service.verify_artifact("artifact.bin", expected_sha256="0" * 64)
    assert mismatch.digest_matches is False
    assert mismatch.verified is False

    too_small = service.verify_artifact("artifact.bin", max_bytes=1)
    assert too_small.size_matches is False
    assert too_small.verified is False


def test_verify_artifact_rejects_bad_expectations_and_paths(tmp_path: Path) -> None:
    (tmp_path / "artifact.txt").write_text("ok", encoding="utf-8")
    service = E2HMCPService(MCPServerConfig(root=tmp_path))
    with pytest.raises(MCPServiceError, match="expected_sha256"):
        service.verify_artifact("artifact.txt", expected_sha256="bad")
    with pytest.raises(MCPServiceError, match="min_bytes"):
        service.verify_artifact("artifact.txt", min_bytes=-1)
    with pytest.raises(MCPServiceError, match="max_bytes"):
        service.verify_artifact("artifact.txt", max_bytes=-1)
    with pytest.raises(MCPServiceError, match="must not exceed"):
        service.verify_artifact("artifact.txt", min_bytes=10, max_bytes=1)
    with pytest.raises(MCPServiceError, match="escapes"):
        service.verify_artifact("../outside")
    with pytest.raises(MCPServiceError, match="regular file"):
        service.verify_artifact(".")


def test_verify_artifact_enforces_operator_byte_limit(tmp_path: Path) -> None:
    (tmp_path / "large.bin").write_bytes(b"12345")
    service = E2HMCPService(MCPServerConfig(root=tmp_path, max_artifact_bytes=4))
    with pytest.raises(MCPServiceError, match="configured max bytes"):
        service.verify_artifact("large.bin")


def test_verify_snapshot_returns_content_addressed_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("a", encoding="utf-8")
    archive = tmp_path / "artifact.e2hsnap"
    manifest = create_snapshot(source, archive)
    service = E2HMCPService(MCPServerConfig(root=tmp_path))
    result = service.verify_snapshot("artifact.e2hsnap")
    assert result.snapshot_id == manifest.snapshot_id
    assert result.entries == 1
    assert result.total_bytes == 1
    assert result.verified is True


def test_verify_snapshot_rejects_missing_or_invalid_archive(tmp_path: Path) -> None:
    service = E2HMCPService(MCPServerConfig(root=tmp_path))
    with pytest.raises(MCPServiceError, match="does not exist"):
        service.verify_snapshot("missing.e2hsnap")
    (tmp_path / "bad.e2hsnap").write_text("not a zip", encoding="utf-8")
    with pytest.raises(MCPServiceError, match="unable to open"):
        service.verify_snapshot("bad.e2hsnap")


def test_replay_is_operator_gated_and_digest_only_by_default(tmp_path: Path) -> None:
    capsule = tmp_path / "capsule.json"
    _write_capsule(capsule)
    disabled = E2HMCPService(MCPServerConfig(root=tmp_path))
    with pytest.raises(MCPServiceError, match="disabled"):
        disabled.replay("capsule.json")

    if not handle_bound_local_replay_supported():
        return

    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.LOCAL,
        )
    )
    result = service.replay("capsule.json")
    assert result.status == "passed"
    assert len(result.capsule_sha256) == 64
    assert len(result.replay_sha256) == 64
    assert result.checks[0].stdout is None
    assert result.checks[0].stderr is None
    assert result.checks[0].stdout_chars == len("mcp-ok\n")
    assert result.checks[0].stdout_sha256 == hashlib.sha256(b"mcp-ok\n").hexdigest()
    assert result.output_exposed is False


def test_replay_output_requires_operator_opt_in(tmp_path: Path) -> None:
    if not handle_bound_local_replay_supported():
        pytest.skip("handle-bound MCP local replay is unavailable on this host")
    _write_capsule(tmp_path / "capsule.json")
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.LOCAL,
            expose_replay_output=True,
        )
    )
    result = service.replay("capsule.json")
    assert result.checks[0].stdout == "mcp-ok\n"
    assert result.output_exposed is True


def test_replay_rejects_paths_and_missing_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)
    service = E2HMCPService(MCPServerConfig(root=tmp_path, allow_replay=True))
    with pytest.raises(MCPServiceError, match="capsule escapes"):
        service.replay("../capsule.json")
    with pytest.raises(MCPServiceError, match="capsule does not exist"):
        service.replay("missing.json")
    (tmp_path / "capsule.json").write_text("{}", encoding="utf-8")
    with pytest.raises(MCPServiceError, match="workspace does not exist"):
        service.replay("capsule.json", workspace="missing")


def test_mcp_tool_catalog_hides_replay_unless_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)

    async def scenario() -> None:
        server = create_mcp_server(MCPServerConfig(root=tmp_path))
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert {
                "e2h_status",
                "e2h_memory_query",
                "e2h_verify_artifact",
                "e2h_verify_snapshot",
            } <= names
            assert "e2h_replay" not in names
            status = await client.call_tool("e2h_status", {})
            assert status.is_error is False
            assert status.structured_content is not None
            assert status.structured_content["replay_enabled"] is False

        replay_server = create_mcp_server(
            MCPServerConfig(root=tmp_path, allow_replay=True, replay_backend=ExecutionBackend.LOCAL)
        )
        async with Client(replay_server) as client:
            tools = await client.list_tools()
            assert "e2h_replay" in {tool.name for tool in tools.tools}

    asyncio.run(scenario())


def test_mcp_memory_and_artifact_tools_return_structured_results(tmp_path: Path) -> None:
    database = tmp_path / "memory.duckdb"
    initialize_store(database)
    artifact = tmp_path / "proof.txt"
    artifact.write_text("proof", encoding="utf-8")

    async def scenario() -> None:
        server = create_mcp_server(MCPServerConfig(root=tmp_path, store=Path("memory.duckdb")))
        async with Client(server) as client:
            memory = await client.call_tool("e2h_memory_query", {"view": "sources", "limit": 2})
            assert memory.is_error is False
            assert memory.structured_content is not None
            assert memory.structured_content["row_count"] == 0

            verification = await client.call_tool(
                "e2h_verify_artifact",
                {"artifact": "proof.txt", "expected_sha256": hashlib.sha256(b"proof").hexdigest()},
            )
            assert verification.is_error is False
            assert verification.structured_content is not None
            assert verification.structured_content["verified"] is True

    asyncio.run(scenario())
