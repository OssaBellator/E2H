"""Fail-closed Docker daemon capability probes for remote replay."""

from __future__ import annotations

import subprocess

from e2h.docker_remote import DockerRemoteError

_RESOURCE_TIMEOUT_SECONDS = 10.0
_RESOURCE_FORMAT = (
    "{{.OSType}} {{.MemoryLimit}} {{.SwapLimit}} "
    "{{.CpuCfsPeriod}} {{.CpuCfsQuota}} {{.PidsLimit}}"
)


def require_docker_resource_limits(runtime_binary: str = "docker") -> None:
    """Require a Linux Docker daemon with replay resource-limit enforcement."""
    if not runtime_binary or "\x00" in runtime_binary:
        raise DockerRemoteError("Docker runtime binary must be non-empty and contain no NUL")
    try:
        completed = subprocess.run(
            [runtime_binary, "info", "--format", _RESOURCE_FORMAT],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_RESOURCE_TIMEOUT_SECONDS,
            check=False,
            text=True,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise DockerRemoteError(
            f"unable to query Docker resource capabilities: {exc}"
        ) from exc
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip() or "unknown Docker error"
        raise DockerRemoteError(
            "Docker resource capability probe failed with exit "
            f"{completed.returncode}: {error}"
        )

    fields = completed.stdout.strip().split()
    if len(fields) != 6 or any(field not in {"true", "false"} for field in fields[1:]):
        raise DockerRemoteError("Docker resource capability probe returned an unexpected response")
    (
        os_type,
        memory_limit,
        swap_limit,
        cpu_cfs_period,
        cpu_cfs_quota,
        pids_limit,
    ) = fields
    if os_type != "linux":
        raise DockerRemoteError(
            "remote Docker replay requires a Linux daemon; "
            f"observed os_type={os_type}"
        )
    if memory_limit != "true" or swap_limit != "true":
        raise DockerRemoteError(
            "remote Docker replay requires memory and swap limit support; "
            f"observed memory_limit={memory_limit}, swap_limit={swap_limit}"
        )
    if (
        cpu_cfs_period != "true"
        or cpu_cfs_quota != "true"
        or pids_limit != "true"
    ):
        raise DockerRemoteError(
            "remote Docker replay requires CPU CFS period/quota and PID limit support; "
            f"observed cpu_cfs_period={cpu_cfs_period}, "
            f"cpu_cfs_quota={cpu_cfs_quota}, pids_limit={pids_limit}"
        )
