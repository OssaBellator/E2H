from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.compiler import CompilerSpec, EnvironmentMutation, GoalSelector
from e2h.models import CommandCheck


def _check() -> CommandCheck:
    return CommandCheck(id="contract", argv=["python", "-V"])


def test_compiler_spec_revalidates_mutated_goal_trace_id() -> None:
    goal = GoalSelector()
    goal.trace_id = "x" * 257

    with pytest.raises(ValidationError) as exc_info:
        CompilerSpec(id="compiler", goal=goal, checks=[_check()])

    assert exc_info.value.errors()[0]["loc"][-1] == "trace_id"


def test_compiler_spec_revalidates_mutated_environment_mutation() -> None:
    mutation = EnvironmentMutation(
        id="mutation-1",
        env={"MODE": "mutated"},
        check_ids=["contract"],
    )
    mutation.check_ids = ["contract", "contract"]

    with pytest.raises(ValidationError, match="mutation check_ids must be unique"):
        CompilerSpec(id="compiler", checks=[_check()], mutations=[mutation])
