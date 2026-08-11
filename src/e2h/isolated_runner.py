"""Isolated workspace helpers for future remote container replay."""

from __future__ import annotations

import os
from pathlib import Path

from e2h.directory_binding import directory_binding_supported
from e2h.docker_remote import DockerRemoteError, prepared_workspace_volume
from e2h.models import TaskCapsule
from e2h.runner import RunnerError, RunResult, _validated_capsule
from e2h.volume_runner import run_capsule_prepared_volume
from e2h.workspace_archive import (
    _MAX_ARCHIVE_MEMBER_PATH_BYTES,
    WorkspaceArchive,
    WorkspaceArchiveError,
    stable_workspace_archive,
)

# Source bytes can appear once as file/link payload and again as PAX string metadata.
# Four times the capture path cap leaves room per entry for long PAX names, tar/PAX
# headers, and block padding; the fixed allowance covers the root member and trailer.
_REMOTE_ARCHIVE_ENTRY_OVERHEAD_BYTES = 4 * _MAX_ARCHIVE_MEMBER_PATH_BYTES
_REMOTE_ARCHIVE_FIXED_OVERHEAD_BYTES = 1024 * 1024


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


def _max_remote_archive_bytes(max_source_bytes: int, max_entries: int) -> int:
    """Return a conservative transfer cap for the uncompressed PAX workspace tar."""
    if max_source_bytes < 1 or max_entries < 1:
        raise RunnerError("remote workspace archive limits must be positive")
    return (
        2 * max_source_bytes
        + max_entries * _REMOTE_ARCHIVE_ENTRY_OVERHEAD_BYTES
        + _REMOTE_ARCHIVE_FIXED_OVERHEAD_BYTES
    )


def _validate_remote_archive_resources(
    archive: WorkspaceArchive,
    *,
    max_source_bytes: int,
    max_entries: int,
) -> None:
    """Reject unexpected tar amplification before any bytes reach the Docker daemon."""
    if archive.source_bytes > max_source_bytes or archive.entries > max_entries:
        raise RunnerError("sealed workspace archive metadata exceeds configured capture limits")
    max_archive_bytes = _max_remote_archive_bytes(max_source_bytes, max_entries)
    if archive.archive_bytes > max_archive_bytes:
        raise RunnerError(
            "sealed workspace archive exceeds derived transfer bound "
            f"({max_archive_bytes} bytes)"
        )


def _run_capsule_isolated_container_candidate(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    max_workspace_bytes: int,
    max_workspace_entries: int,
    container_runtime: str | None = None,
) -> RunResult:
    """Exercise the sealed-volume design without enabling the remote capability gate."""
    capsule = _validated_capsule(capsule)
    sandbox = capsule.sandbox
    if sandbox is None:
        raise RunnerError("isolated container replay requires capsule.sandbox")
    runtime = container_runtime or sandbox.engine
    try:
        with stable_workspace_archive(
            workspace,
            max_bytes=max_workspace_bytes,
            max_entries=max_workspace_entries,
        ) as archive:
            _validate_remote_archive_resources(
                archive,
                max_source_bytes=max_workspace_bytes,
                max_entries=max_workspace_entries,
            )
            with prepared_workspace_volume(
                sandbox,
                archive,
                runtime_binary=runtime,
            ) as volume_name:
                return run_capsule_prepared_volume(
                    capsule,
                    archive,
                    volume_name,
                    container_runtime=runtime,
                )
    except (WorkspaceArchiveError, DockerRemoteError) as exc:
        raise RunnerError(str(exc)) from exc


def run_capsule_isolated_container(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    max_workspace_bytes: int,
    max_workspace_entries: int,
    container_runtime: str | None = None,
) -> RunResult:
    """Fail closed while real Docker runtime validation remains incomplete."""
    del capsule, workspace, max_workspace_bytes, max_workspace_entries, container_runtime
    raise RunnerError(
        "isolated container replay is unavailable until the sealed-volume path is validated "
        "against a real patched Docker runtime"
    )
