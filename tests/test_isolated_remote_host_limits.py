from __future__ import annotations

from pathlib import Path

import pytest

import e2h.isolated_runner as isolated_runner
from e2h.isolated_runner import _validate_remote_host_limits
from e2h.models import (
    CommandCheck,
    ContainerSandbox,
    ExecutionLimits,
    SuccessSpec,
    TaskCapsule,
)
from e2h.runner import RunnerError

IMAGE = "python@sha256:" + "0" * 64


def _capsule(
    count: int,
    *,
    max_output_chars: int = 20_000,
    default_timeout_seconds: float = 30.0,
    per_check_timeout: float | None = None,
) -> TaskCapsule:
    return TaskCapsule(
        id="remote-host-budget",
        goal="Bound host-side remote replay work.",
        limits=ExecutionLimits(
            max_commands=count,
            max_output_chars=max_output_chars,
            default_timeout_seconds=default_timeout_seconds,
        ),
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id=f"check-{index}",
                    argv=["check"],
                    timeout_seconds=per_check_timeout,
                )
                for index in range(count)
            ]
        ),
    )


def test_default_maximum_command_envelope_fits_remote_host_budgets() -> None:
    _validate_remote_host_limits(_capsule(50))


def test_remote_host_budget_rejects_too_many_commands() -> None:
    with pytest.raises(RunnerError, match="host command budget"):
        _validate_remote_host_limits(_capsule(51))


def test_remote_host_budget_rejects_aggregate_retained_output() -> None:
    with pytest.raises(RunnerError, match="aggregate retained-output budget"):
        _validate_remote_host_limits(_capsule(2, max_output_chars=500_001))


def test_remote_host_budget_rejects_aggregate_check_timeout() -> None:
    with pytest.raises(RunnerError, match="aggregate check-timeout budget"):
        _validate_remote_host_limits(_capsule(2, per_check_timeout=901.0))


def test_remote_host_budget_fails_before_docker_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_probe(runtime: str) -> None:
        del runtime
        raise AssertionError("Docker must not be probed for an oversized remote replay")

    monkeypatch.setattr(isolated_runner, "require_patched_docker_archive", forbidden_probe)

    with pytest.raises(RunnerError, match="host command budget"):
        isolated_runner._run_capsule_isolated_container_candidate(
            _capsule(51),
            tmp_path / "unused-workspace",
            max_workspace_bytes=1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )
