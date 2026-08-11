"""Isolated container replay backed by a private descriptor-bound workspace copy."""

from __future__ import annotations

import os
from pathlib import Path

from e2h.directory_binding import directory_binding_supported
from e2h.models import TaskCapsule
from e2h.runner import ExecutionBackend, RunnerError, RunResult, run_capsule
from e2h.workspace_snapshot import stable_workspace_snapshot


def isolated_container_replay_supported() -> bool:
    """Return whether the current host exposes every primitive required for safe capture."""
    return (
        directory_binding_supported()
        and os.stat in os.supports_dir_fd
        and os.readlink in os.supports_dir_fd
        and os.listdir in os.supports_fd
    )


def _validate_isolated_remote_policy(capsule: TaskCapsule) -> None:
    sandbox = capsule.sandbox
    if sandbox is None:
        raise RunnerError("isolated container replay requires capsule.sandbox")
    if sandbox.workspace_access != "read_only":
        raise RunnerError(
            "isolated container replay requires sandbox.workspace_access='read_only'"
        )
    if not sandbox.read_only_root:
        raise RunnerError("isolated container replay requires sandbox.read_only_root=true")
    if sandbox.pull_policy != "never":
        raise RunnerError("isolated container replay requires sandbox.pull_policy='never'")


def run_capsule_isolated_container(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    max_workspace_bytes: int,
    max_workspace_entries: int,
    container_runtime: str | None = None,
) -> RunResult:
    """Run a container capsule in a bounded private copy of the supplied workspace."""
    _validate_isolated_remote_policy(capsule)
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
