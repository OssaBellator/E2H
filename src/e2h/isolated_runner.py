"""Isolated container replay backed by a private descriptor-bound workspace copy."""

from __future__ import annotations

from pathlib import Path

from e2h.models import TaskCapsule
from e2h.runner import ExecutionBackend, RunResult, run_capsule
from e2h.workspace_snapshot import stable_workspace_snapshot, workspace_snapshot_supported


def isolated_container_replay_supported() -> bool:
    """Return whether the current host can capture stable isolated replay workspaces."""
    return workspace_snapshot_supported()


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
