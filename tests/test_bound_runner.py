from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

import e2h.directory_binding as directory_binding
from e2h.bound_runner import run_capsule_bound_local
from e2h.directory_binding import bound_absolute_directory
from e2h.models import TaskCapsule
from e2h.runner import CheckStatus, RunnerError, RunStatus

pytestmark = pytest.mark.skipif(
    not directory_binding._DIRECTORY_BINDING_SUPPORTED or not sys.platform.startswith("linux"),
    reason="handle-bound local replay requires Linux directory descriptors and procfs",
)


def _capsule(commands: list[dict[str, object]], **extra: object) -> TaskCapsule:
    payload: dict[str, object] = {
        "id": "bound-runner",
        "goal": "Exercise handle-bound replay.",
        "success": {"commands": commands},
    }
    payload.update(extra)
    return TaskCapsule.model_validate(payload)


def _run_bound(capsule: TaskCapsule, workspace: Path):
    with bound_absolute_directory(workspace.resolve()) as descriptor:
        return run_capsule_bound_local(
            capsule,
            workspace,
            workspace_descriptor=descriptor,
        )


def test_bound_runner_executes_in_nested_check_directory(tmp_path: Path) -> None:
    nested = tmp_path / "task" / "nested"
    nested.mkdir(parents=True)
    capsule = _capsule(
        [
            {
                "id": "cwd",
                "cwd": "nested",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('proof').write_text(Path.cwd().name)",
                ],
            }
        ],
        initial_state={"working_directory": "task"},
    )

    result = _run_bound(capsule, tmp_path)

    assert result.status is RunStatus.PASSED
    assert (nested / "proof").read_text(encoding="utf-8") == "nested"
    assert result.checks[0].cwd == "task/nested"


def test_bound_runner_ignores_workspace_path_rebinding_after_bind(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    moved = tmp_path / "original-workspace"
    capsule = _capsule(
        [
            {
                "id": "write",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('proof').write_text('inside')",
                ],
            }
        ]
    )

    with bound_absolute_directory(workspace.resolve()) as descriptor:
        workspace.rename(moved)
        try:
            workspace.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
        result = run_capsule_bound_local(
            capsule,
            workspace,
            workspace_descriptor=descriptor,
        )

    assert result.status is RunStatus.PASSED
    assert (moved / "proof").read_text(encoding="utf-8") == "inside"
    assert not (outside / "proof").exists()


def test_bound_runner_preserves_safe_in_workspace_symlink_cwd(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    capsule = _capsule(
        [
            {
                "id": "linked",
                "cwd": "alias",
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('proof').write_text('safe')",
                ],
            }
        ]
    )

    result = _run_bound(capsule, tmp_path)

    assert result.status is RunStatus.PASSED
    assert (target / "proof").read_text(encoding="utf-8") == "safe"


def test_bound_runner_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    escape = tmp_path / "escape"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    capsule = _capsule(
        [{"id": "escape", "cwd": "escape", "argv": [sys.executable, "-V"]}]
    )

    with pytest.raises(RunnerError, match="escapes bound workspace"):
        _run_bound(capsule, tmp_path)


def test_bound_runner_preserves_missing_command_classification(tmp_path: Path) -> None:
    capsule = _capsule(
        [{"id": "missing", "argv": ["e2h-command-that-does-not-exist"]}]
    )

    result = _run_bound(capsule, tmp_path)

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code.value == "command_not_found"


def test_bound_runner_timeout_terminates_descendants(tmp_path: Path) -> None:
    sentinel = tmp_path / "descendant-survived"
    child_code = (
        "import time; from pathlib import Path; "
        f"time.sleep(0.4); Path({str(sentinel)!r}).write_text('alive', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(10)"
    )
    capsule = _capsule(
        [
            {
                "id": "spawns-child",
                "argv": [sys.executable, "-c", parent_code],
                "timeout_seconds": 0.05,
            }
        ]
    )

    result = _run_bound(capsule, tmp_path)
    time.sleep(0.6)

    assert result.checks[0].status is CheckStatus.TIMED_OUT
    assert not sentinel.exists()


def test_bound_runner_missing_check_directory_is_error(tmp_path: Path) -> None:
    capsule = _capsule(
        [{"id": "missing", "cwd": "missing", "argv": [sys.executable, "-V"]}]
    )

    result = _run_bound(capsule, tmp_path)

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
