"""Isolated container replay backed by a private descriptor-bound workspace copy."""

from __future__ import annotations

import os
from pathlib import Path

from e2h.directory_binding import directory_binding_supported
from e2h.models import TaskCapsule
from e2h.runner import ExecutionBackend, RunResult, run_capsule
from e2h.workspace_snapshot import stable_workspace_snapshot


def isolated_container_replay_supported() -> bool:
    """Return whether the current host exposes every primitive required for safe capture."""
    return (
        directory_binding_supported()
        and os.stat in os.supports_dir_fd
        and os.readlink in os.supports_dir_fd
        and os.listdir in os.supports_fd
    )


def run_capsule_isolated_container(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    max_workspace_bytes: int,
    max_workspace_entries: int,
    container_runtime: str | None = None,
) -> RunResult:
    """Run a container capsule in a bounded private copy of the supplied workspace."""
    with stable_workspace_snapshot(
        workspace,
        max_bytes=max_workspace_bytes,
        max_entries=max_workspace_entries,
    ) as private_workspace:
        return run_capsule(
            capsule,
            private_workspace,
            backend=ExecutionBackend.CONTAINER,
            container_runtime=container_runtime,
        )
