from __future__ import annotations

from pathlib import Path
from typing import Any

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


def test_runner_wraps_workspace_child_resolution_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_resolve = Path.resolve

    def failing_resolve(path: Path, *args: Any, **kwargs: Any) -> Path:
        if path.name == "broken":
            raise OSError("injected resolution failure")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)

    with pytest.raises(RunnerError, match="unable to resolve workspace path"):
        run_capsule(_capsule(working_directory="broken"), tmp_path)
