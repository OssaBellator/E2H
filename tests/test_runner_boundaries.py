from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest

import e2h.runner as runner
from e2h.models import TaskCapsule

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _CapsuleSubclass(TaskCapsule):
    pass


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runner-boundary",
            "goal": "Exercise the runner trust boundary.",
            "success": {
                "commands": [
                    {
                        "id": "first",
                        "argv": [sys.executable, "-c", "print('first')"],
                    },
                    {
                        "id": "second",
                        "argv": [sys.executable, "-c", "print('second')"],
                    },
                ]
            },
        }
    )


def successful_outcome() -> runner._ProcessOutcome:
    return runner._ProcessOutcome(
        exit_code=0,
        timed_out=False,
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
    )


def test_run_revalidates_mutated_command_before_execution(tmp_path: Path) -> None:
    candidate = capsule()
    candidate.success.commands[0].argv = []

    with pytest.raises(runner.RunnerError, match="invalid task capsule"):
        runner.run_capsule(candidate, tmp_path)


def test_run_revalidates_mutated_capsule_cross_fields(tmp_path: Path) -> None:
    candidate = capsule()
    candidate.limits.max_commands = 1

    with pytest.raises(
        runner.RunnerError,
        match=r"success\.commands exceeds limits\.max_commands",
    ):
        runner.run_capsule(candidate, tmp_path)


def test_run_rejects_capsule_subclasses_and_plain_wrong_types(tmp_path: Path) -> None:
    candidate = capsule()
    subclassed = _CapsuleSubclass.model_validate(candidate.model_dump(mode="json"))

    with pytest.raises(
        runner.RunnerError,
        match="expected TaskCapsule, got _CapsuleSubclass",
    ):
        runner.run_capsule(subclassed, tmp_path)

    with pytest.raises(runner.RunnerError, match="expected TaskCapsule, got object"):
        runner.run_capsule(cast(Any, object()), tmp_path)


def test_run_normalizes_warning_prone_raw_command_assignment(tmp_path: Path) -> None:
    candidate = capsule()
    candidate.success.commands = [
        {
            "id": "raw",
            "argv": [sys.executable, "-c", "print('normalized')"],
        }
    ]

    result = runner.run_capsule(candidate, tmp_path)

    assert result.status is runner.RunStatus.PASSED
    assert result.checks[0].id == "raw"
    assert result.checks[0].stdout == "normalized\n"


def test_run_uses_detached_capsule_snapshot_during_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = capsule()
    original_argv = [list(check.argv) for check in candidate.success.commands]
    observed: list[tuple[str, float, int, list[str]]] = []

    def fake_execute(
        check: Any,
        cwd: Path,
        timeout: float,
        max_output_chars: int,
    ) -> runner._ProcessOutcome:
        del cwd
        observed.append((check.id, timeout, max_output_chars, list(check.argv)))
        if check.id == "first":
            candidate.id = "caller-mutated"
            candidate.success.commands[1].argv = ["caller-mutated-command"]
            candidate.limits.default_timeout_seconds = 1.0
            candidate.limits.max_output_chars = 256
        return successful_outcome()

    monkeypatch.setattr(runner, "_execute_local_command", fake_execute)

    result = runner.run_capsule(candidate, tmp_path)

    assert result.status is runner.RunStatus.PASSED
    assert result.capsule_id == "runner-boundary"
    assert [check.argv for check in result.checks] == original_argv
    assert [item[1] for item in observed] == [30.0, 30.0]
    assert [item[2] for item in observed] == [20_000, 20_000]
    assert [item[3] for item in observed] == original_argv
