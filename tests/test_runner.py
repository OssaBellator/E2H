from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from e2h.models import TaskCapsule
from e2h.runner import _BoundedCapture, CheckStatus, RunnerError, RunStatus, run_capsule


def make_capsule(commands: list[dict[str, object]], **limits: object) -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runner-test",
            "goal": "Exercise the runner",
            "limits": limits or {},
            "success": {"commands": commands},
        }
    )


def test_run_passes_and_captures_output(tmp_path: Path) -> None:
    capsule = make_capsule([{"id": "hello", "argv": [sys.executable, "-c", "print('hello')"]}])
    result = run_capsule(capsule, tmp_path)
    assert result.status is RunStatus.PASSED
    assert result.checks[0].stdout == "hello\n"


def test_failure_halts_and_marks_remaining_checks_skipped(tmp_path: Path) -> None:
    capsule = make_capsule(
        [
            {"id": "fail", "argv": [sys.executable, "-c", "raise SystemExit(7)"]},
            {"id": "later", "argv": [sys.executable, "-c", "print('not run')"]},
        ]
    )
    result = run_capsule(capsule, tmp_path)
    assert result.status is RunStatus.FAILED
    assert [check.status for check in result.checks] == [CheckStatus.FAILED, CheckStatus.SKIPPED]
    assert result.checks[0].exit_code == 7


def test_continue_on_failure_runs_next_check(tmp_path: Path) -> None:
    capsule = make_capsule(
        [
            {
                "id": "expected-failure",
                "argv": [sys.executable, "-c", "raise SystemExit(2)"],
                "continue_on_failure": True,
            },
            {"id": "later", "argv": [sys.executable, "-c", "print('ran')"]},
        ]
    )
    result = run_capsule(capsule, tmp_path)
    assert result.status is RunStatus.FAILED
    assert result.checks[1].status is CheckStatus.PASSED


def test_timeout_is_reported(tmp_path: Path) -> None:
    capsule = make_capsule(
        [
            {
                "id": "slow",
                "argv": [sys.executable, "-c", "import time; time.sleep(1)"],
                "timeout_seconds": 0.05,
            }
        ]
    )
    result = run_capsule(capsule, tmp_path)
    assert result.status is RunStatus.FAILED
    assert result.checks[0].status is CheckStatus.TIMED_OUT


def test_output_is_bounded(tmp_path: Path) -> None:
    capsule = make_capsule(
        [{"id": "large", "argv": [sys.executable, "-c", "print('x' * 1000)"]}],
        max_output_chars=256,
    )
    result = run_capsule(capsule, tmp_path)
    assert result.checks[0].stdout_truncated is True
    assert len(result.checks[0].stdout) == 256


def test_capture_retains_bounded_memory() -> None:
    capture = _BoundedCapture(256)
    for _ in range(1024):
        capture.feed(b"x" * 4096)
    rendered, truncated = capture.render()
    assert capture.retained_bytes <= 1024
    assert truncated is True
    assert len(rendered) == 256


@pytest.mark.skipif(os.name != "posix", reason="process-group termination is POSIX-specific")
def test_timeout_terminates_descendant_processes(tmp_path: Path) -> None:
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
    capsule = make_capsule(
        [
            {
                "id": "spawns-child",
                "argv": [sys.executable, "-c", parent_code],
                "timeout_seconds": 0.05,
            }
        ]
    )
    result = run_capsule(capsule, tmp_path)
    time.sleep(0.6)
    assert result.checks[0].status is CheckStatus.TIMED_OUT
    assert not sentinel.exists()


def test_missing_command_is_infrastructure_error(tmp_path: Path) -> None:
    capsule = make_capsule([{"id": "missing", "argv": ["e2h-command-that-does-not-exist"]}])
    result = run_capsule(capsule, tmp_path)
    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR


def test_rejects_workspace_escape_through_symlink(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    capsule = TaskCapsule.model_validate(
        {
            "id": "escape",
            "goal": "Do not escape",
            "initial_state": {"working_directory": "escape"},
            "success": {"commands": [{"id": "ok", "argv": [sys.executable, "-V"]}]},
        }
    )
    with pytest.raises(RunnerError, match="escapes"):
        run_capsule(capsule, tmp_path)


def test_rejects_missing_workspace(tmp_path: Path) -> None:
    capsule = make_capsule([{"id": "ok", "argv": [sys.executable, "-V"]}])
    with pytest.raises(RunnerError, match="workspace is not a directory"):
        run_capsule(capsule, tmp_path / "missing")


def test_missing_task_working_directory_is_rejected(tmp_path: Path) -> None:
    capsule = TaskCapsule.model_validate(
        {
            "id": "missing-dir",
            "goal": "Reject a missing directory",
            "initial_state": {"working_directory": "missing"},
            "success": {"commands": [{"id": "ok", "argv": [sys.executable, "-V"]}]},
        }
    )
    with pytest.raises(RunnerError, match="working directory does not exist"):
        run_capsule(capsule, tmp_path)


def test_missing_check_directory_is_an_error(tmp_path: Path) -> None:
    capsule = make_capsule(
        [{"id": "missing-cwd", "cwd": "missing", "argv": [sys.executable, "-V"]}]
    )
    result = run_capsule(capsule, tmp_path)
    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
