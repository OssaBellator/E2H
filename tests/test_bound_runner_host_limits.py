from __future__ import annotations

from pathlib import Path

import pytest

from e2h.bound_runner import run_capsule_bound_local
from e2h.models import CommandCheck, ExecutionLimits, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError


def test_bound_local_host_budget_fails_before_workspace_inspection(tmp_path: Path) -> None:
    command_count = 51
    capsule = TaskCapsule(
        id="bound-local-host-budget",
        goal="Reject oversized remote-service replay before touching the workspace.",
        limits=ExecutionLimits(max_commands=command_count),
        success=SuccessSpec(
            commands=[
                CommandCheck(id=f"check-{index}", argv=["check"])
                for index in range(command_count)
            ]
        ),
    )

    with pytest.raises(RunnerError, match="host command budget"):
        run_capsule_bound_local(
            capsule,
            tmp_path / "unused-workspace",
            workspace_descriptor=-1,
        )
