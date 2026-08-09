from __future__ import annotations

from pathlib import Path

import pytest

from e2h.models import TaskCapsule
from e2h.runner import RunnerError, run_capsule


def _capsule(*, working_directory: str = ".") -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runner-path-boundary",
            "goal": "Validate runner workspace paths.",
            "initial_state": {"working_directory": working_directory},
            "success": {"commands": [{"id": "check", "argv": ["python", "-V"]}]},
        }
    )


def test_runner_rejects_nul_workspace_before_filesystem_access() -> None:
    with pytest.raises(RunnerError, match="workspace path must not contain NUL"):
        run_capsule(_capsule(), Path("bad\x00workspace"))


def test_runner_wraps_workspace_child_resolution_failure(tmp_path: Path) -> None:
    loop = tmp_path / "loop"
    try:
        loop.symlink_to("loop")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(RunnerError, match="unable to resolve workspace path"):
        run_capsule(_capsule(working_directory="loop"), tmp_path)
