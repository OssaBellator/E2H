from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

import e2h.mcp_server as mcp_server
from e2h.failures import summarize_failures
from e2h.mcp_server import E2HMCPService, MCPServerConfig
from e2h.runner import ExecutionBackend, RunResult, RunStatus


def _capsule(path: Path, *, sandbox: bool) -> None:
    payload: dict[str, object] = {
        "id": "replay-semantics",
        "goal": "Report replay workspace semantics.",
        "success": {"commands": [{"id": "check", "argv": ["python", "-V"]}]},
    }
    if sandbox:
        payload["sandbox"] = {"image": "python@sha256:" + "0" * 64}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _result() -> RunResult:
    now = datetime.now(UTC)
    return RunResult(
        capsule_id="replay-semantics",
        status=RunStatus.PASSED,
        started_at=now,
        finished_at=now,
        duration_seconds=0,
        checks=[],
        failure_summary=summarize_failures([]),
    )


@contextmanager
def _fake_bind(path: Path, expected: Path) -> Iterator[int]:
    assert path == expected.resolve()
    yield 73


def test_local_replay_reports_persistent_bound_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = tmp_path / "capsule.json"
    _capsule(capsule, sandbox=False)
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)

    @contextmanager
    def fake_bind(path: Path) -> Iterator[int]:
        with _fake_bind(path, tmp_path) as descriptor:
            yield descriptor

    monkeypatch.setattr(mcp_server, "bound_absolute_directory", fake_bind)
    monkeypatch.setattr(mcp_server, "run_capsule_bound_local", lambda *args, **kwargs: _result())
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.LOCAL,
        )
    )

    result = service.replay(capsule.name)

    assert result.execution_backend == "local"
    assert result.workspace_mode == "bound_local"
    assert result.workspace_mutations_persisted is True


def test_container_replay_reports_discarded_isolated_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = tmp_path / "capsule.json"
    _capsule(capsule, sandbox=True)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: True)
    monkeypatch.setattr(
        mcp_server,
        "run_capsule_isolated_container",
        lambda *args, **kwargs: _result(),
    )
    service = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.CONTAINER,
            trusted_container_control_plane=True,
        )
    )

    result = service.replay(capsule.name)

    assert result.execution_backend == "container"
    assert result.workspace_mode == "isolated_copy"
    assert result.workspace_mutations_persisted is False


def test_replay_digest_binds_workspace_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_capsule = tmp_path / "local.json"
    container_capsule = tmp_path / "container.json"
    _capsule(local_capsule, sandbox=False)
    _capsule(container_capsule, sandbox=True)
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)
    monkeypatch.setattr(mcp_server, "isolated_container_replay_supported", lambda: True)

    @contextmanager
    def fake_bind(path: Path) -> Iterator[int]:
        with _fake_bind(path, tmp_path) as descriptor:
            yield descriptor

    shared_result = _result()
    monkeypatch.setattr(mcp_server, "bound_absolute_directory", fake_bind)
    monkeypatch.setattr(
        mcp_server,
        "run_capsule_bound_local",
        lambda *args, **kwargs: shared_result,
    )
    monkeypatch.setattr(
        mcp_server,
        "run_capsule_isolated_container",
        lambda *args, **kwargs: shared_result,
    )
    local = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.LOCAL,
        )
    )
    container = E2HMCPService(
        MCPServerConfig(
            root=tmp_path,
            allow_replay=True,
            replay_backend=ExecutionBackend.CONTAINER,
            trusted_container_control_plane=True,
        )
    )

    local_result = local.replay(local_capsule.name)
    container_result = container.replay(container_capsule.name)

    assert local_result.replay_sha256 != container_result.replay_sha256
