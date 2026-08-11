from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from e2h.bound_runner import handle_bound_local_replay_supported, run_capsule_bound_local
from e2h.directory_binding import bound_absolute_directory
from e2h.models import TaskCapsule
from e2h.runner import ExecutionBackend, RunResult, run_capsule

pytestmark = pytest.mark.skipif(
    not handle_bound_local_replay_supported(),
    reason="handle-bound local replay requires supported Linux directory handles and procfs",
)


def _capsule(commands: list[dict[str, object]], **extra: object) -> TaskCapsule:
    payload: dict[str, object] = {
        "id": "bound-parity",
        "goal": "Compare stable local replay semantics.",
        "success": {"commands": commands},
    }
    payload.update(extra)
    return TaskCapsule.model_validate(payload)


def _run_bound(capsule: TaskCapsule, workspace: Path) -> RunResult:
    with bound_absolute_directory(workspace.resolve()) as descriptor:
        return run_capsule_bound_local(
            capsule,
            workspace,
            workspace_descriptor=descriptor,
        )


def _failure(value: Any) -> Any:
    return None if value is None else value.model_dump(mode="json")


def _semantic_result(result: RunResult) -> dict[str, Any]:
    return {
        "capsule_id": result.capsule_id,
        "status": result.status.value,
        "checks": [
            {
                "id": check.id,
                "argv": check.argv,
                "cwd": check.cwd,
                "status": check.status.value,
                "exit_code": check.exit_code,
                "stdout": check.stdout,
                "stderr": check.stderr,
                "stdout_truncated": check.stdout_truncated,
                "stderr_truncated": check.stderr_truncated,
                "error": check.error,
                "failure": _failure(check.failure),
            }
            for check in result.checks
        ],
        "failure_summary": result.failure_summary.model_dump(mode="json"),
    }


def _assert_parity(capsule: TaskCapsule, workspace: Path) -> None:
    regular = run_capsule(capsule, workspace, backend=ExecutionBackend.LOCAL)
    bound = _run_bound(capsule, workspace)
    assert _semantic_result(bound) == _semantic_result(regular)


def test_bound_runner_matches_pass_result_and_canonical_cwd(tmp_path: Path) -> None:
    nested = tmp_path / "task" / "nested"
    nested.mkdir(parents=True)
    capsule = _capsule(
        [
            {
                "id": "pass",
                "cwd": "nested",
                "argv": [sys.executable, "-c", "print('ok')"],
            }
        ],
        initial_state={"working_directory": "task"},
    )

    _assert_parity(capsule, tmp_path)


def test_bound_runner_matches_failure_and_symlinked_skipped_cwd(tmp_path: Path) -> None:
    task = tmp_path / "task"
    target = task / "target"
    target.mkdir(parents=True)
    alias = task / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    capsule = _capsule(
        [
            {
                "id": "fail",
                "argv": [sys.executable, "-c", "raise SystemExit(7)"],
            },
            {
                "id": "skipped",
                "cwd": "alias",
                "argv": [sys.executable, "-c", "print('unreachable')"],
            },
        ],
        initial_state={"working_directory": "task"},
    )

    _assert_parity(capsule, tmp_path)


def test_bound_runner_matches_continue_on_failure(tmp_path: Path) -> None:
    capsule = _capsule(
        [
            {
                "id": "fail",
                "argv": [sys.executable, "-c", "raise SystemExit(4)"],
                "continue_on_failure": True,
            },
            {
                "id": "continue",
                "argv": [sys.executable, "-c", "print('continued')"],
            },
        ]
    )

    _assert_parity(capsule, tmp_path)


def test_bound_runner_matches_missing_command_failure(tmp_path: Path) -> None:
    capsule = _capsule(
        [{"id": "missing", "argv": ["e2h-command-that-does-not-exist"]}]
    )

    _assert_parity(capsule, tmp_path)


def test_bound_runner_matches_output_truncation(tmp_path: Path) -> None:
    code = "import sys; print('a' * 500); print('b' * 500, file=sys.stderr)"
    capsule = _capsule(
        [{"id": "output", "argv": [sys.executable, "-c", code]}],
        limits={"max_output_chars": 256},
    )

    _assert_parity(capsule, tmp_path)
