"""Isolated workspace helpers for future remote container replay."""

from __future__ import annotations

from pathlib import Path

from e2h.models import TaskCapsule
from e2h.runner import RunnerError, RunResult
from e2h.workspace_archive import sealed_workspace_archive_supported


def isolated_workspace_snapshot_supported() -> bool:
    """Return whether the host can capture the sealed workspace snapshot this path uses."""
    return sealed_workspace_archive_supported()


def isolated_container_replay_supported() -> bool:
    """Return false until the runtime can consume workspace state by stable identity."""
    return False


def run_capsule_isolated_container(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    max_workspace_bytes: int,
    max_workspace_entries: int,
    container_runtime: str | None = None,
) -> RunResult:
    """Fail closed while container runtimes still consume a mutable host workspace pathname."""
    del capsule, workspace, max_workspace_bytes, max_workspace_entries, container_runtime
    raise RunnerError(
        "isolated container replay is unavailable until the runtime can consume workspace "
        "state without resolving a same-UID-mutable host pathname"
    )
