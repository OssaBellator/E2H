"""Fail-closed Docker daemon capability probes for remote replay."""

from __future__ import annotations

import json
import re
import subprocess

from e2h.docker_remote import DockerRemoteError

_RESOURCE_TIMEOUT_SECONDS = 10.0
_RESOURCE_FORMAT = "{{.MemoryLimit}} {{.SwapLimit}}"
_RUNTIME_COMPONENTS_FORMAT = "{{json .Server.Components}}"
_RUNC_VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)([-+].*)?$")


def _validated_runtime_binary(runtime_binary: str) -> str:
    if not runtime_binary or "\x00" in runtime_binary:
        raise DockerRemoteError("Docker runtime binary must be non-empty and contain no NUL")
    return runtime_binary


def _patched_runc_version(value: str) -> bool:
    match = _RUNC_VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        return False
    suffix = match.group(4) or ""
    if suffix.startswith("-"):
        return False
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    if major > 1:
        return True
    if major != 1:
        return False
    if minor == 3:
        return patch >= 6
    if minor == 4:
        return patch >= 3
    return minor >= 5


def require_patched_docker_runtime(runtime_binary: str = "docker") -> str:
    """Require Docker to expose a patched runc component for remote replay."""
    runtime = _validated_runtime_binary(runtime_binary)
    try:
        completed = subprocess.run(
            [runtime, "version", "--format", _RUNTIME_COMPONENTS_FORMAT],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_RESOURCE_TIMEOUT_SECONDS,
            check=False,
            text=True,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise DockerRemoteError(f"unable to query Docker runtime components: {exc}") from exc
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip() or "unknown Docker error"
        raise DockerRemoteError(
            f"Docker runtime component probe failed with exit {completed.returncode}: {error}"
        )
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise DockerRemoteError("Docker runtime component probe returned invalid JSON") from exc
    if type(payload) is not list:
        raise DockerRemoteError("Docker runtime component probe returned an invalid response")
    versions = [
        component.get("Version")
        for component in payload
        if type(component) is dict and component.get("Name") == "runc"
    ]
    if len(versions) != 1 or type(versions[0]) is not str:
        raise DockerRemoteError("Docker runtime component probe did not identify exactly one runc")
    version = versions[0]
    if not _patched_runc_version(version):
        raise DockerRemoteError(
            "remote Docker replay requires patched runc "
            "(1.3.6+, 1.4.3+, or 1.5.0+); observed "
            f"{version!r}"
        )
    return version


def require_docker_resource_limits(runtime_binary: str = "docker") -> None:
    """Require Docker daemon support for both memory and swap enforcement."""
    runtime = _validated_runtime_binary(runtime_binary)
    try:
        completed = subprocess.run(
            [runtime, "info", "--format", _RESOURCE_FORMAT],
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
    if len(fields) != 2 or any(field not in {"true", "false"} for field in fields):
        raise DockerRemoteError("Docker resource capability probe returned an unexpected response")
    memory_limit, swap_limit = fields
    if memory_limit != "true" or swap_limit != "true":
        raise DockerRemoteError(
            "remote Docker replay requires memory and swap limit support; "
            f"observed memory_limit={memory_limit}, swap_limit={swap_limit}"
        )
