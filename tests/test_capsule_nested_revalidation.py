from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.models import CommandCheck, ExecutionLimits, SuccessSpec, TaskCapsule


def _success() -> SuccessSpec:
    return SuccessSpec(commands=[CommandCheck(id="contract", argv=["python", "-V"])])


def test_task_capsule_revalidates_mutated_nested_command() -> None:
    success = _success()
    success.commands[0].cwd = "../escape"

    with pytest.raises(ValidationError, match="path must not contain parent traversal"):
        TaskCapsule(
            id="capsule",
            goal="Run checks.",
            success=success,
        )


def test_task_capsule_revalidates_mutated_limits() -> None:
    limits = ExecutionLimits(max_commands=10)
    limits.max_commands = 0

    with pytest.raises(ValidationError) as exc_info:
        TaskCapsule(
            id="capsule",
            goal="Run checks.",
            limits=limits,
            success=_success(),
        )

    assert exc_info.value.errors()[0]["loc"][-1] == "max_commands"
