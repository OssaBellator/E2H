"""Isolated workspace helpers for future remote container replay."""

from __future__ import annotations

import os
from pathlib import Path

from e2h.directory_binding import directory_binding_supported
from e2h.models import TaskCapsule
from e2h.runner import RunnerError, RunResult


def isolated_workspace_snapshot_supported() -> bool:
    """Return whether the host exposes every primitive required for safe snapshot capture."""
    return (
        directory_binding_supported()
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.readlink in os.supports_dir_fd
        and os.listdir in os.supports_fd
    )


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
