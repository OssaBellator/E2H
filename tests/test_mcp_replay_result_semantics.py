from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

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


def test_local_replay_reports_persistent_bound_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = tmp_path / "capsule.json"
    _capsule(capsule, sandbox=False)
    monkeypatch.setattr(mcp_server, "handle_bound_local_replay_supported", lambda: True)

    @contextmanager
    def fake_bind(path: Path) -> Iterator[int]:
        assert path == tmp_path.resolve()
        yield 73

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
        )
    )

    result = service.replay(capsule.name)

    assert result.execution_backend == "container"
    assert result.workspace_mode == "isolated_copy"
    assert result.workspace_mutations_persisted is False
