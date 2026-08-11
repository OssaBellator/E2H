"""Docker capability checks for future remote container replay."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

_MIN_DOCKER_ARCHIVE_VERSION = (29, 5, 2)
_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_VERSION_TIMEOUT_SECONDS = 10.0


class DockerRemoteError(RuntimeError):
    """Raised when Docker cannot satisfy the remote replay security boundary."""


@dataclass(frozen=True, order=True)
class DockerVersion:
    major: int
    minor: int
    patch: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def _parse_version(value: str, *, noun: str) -> DockerVersion:
    match = _VERSION_PATTERN.fullmatch(value.strip())
    if match is None:
        raise DockerRemoteError(f"unable to parse Docker {noun} version: {value!r}")
    return DockerVersion(*(int(part) for part in match.groups()))


def _validated_runtime_binary(runtime_binary: str) -> str:
    if not runtime_binary or "\x00" in runtime_binary:
        raise DockerRemoteError("Docker runtime binary must be non-empty and contain no NUL")
    return runtime_binary


def inspect_docker_versions(runtime_binary: str = "docker") -> tuple[DockerVersion, DockerVersion]:
    """Return Docker CLI and daemon versions from one bounded version probe."""
    runtime = _validated_runtime_binary(runtime_binary)
    try:
        completed = subprocess.run(
            [
                runtime,
                "version",
                "--format",
                "{{.Client.Version}} {{.Server.Version}}",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
            text=True,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise DockerRemoteError(f"unable to query Docker runtime version: {exc}") from exc
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip() or "unknown Docker error"
        raise DockerRemoteError(
            f"Docker version probe failed with exit {completed.returncode}: {error}"
        )
    fields = completed.stdout.strip().split()
    if len(fields) != 2:
        raise DockerRemoteError("Docker version probe returned an unexpected response")
    return (
        _parse_version(fields[0], noun="client"),
        _parse_version(fields[1], noun="server"),
    )


def patched_docker_archive_supported(runtime_binary: str = "docker") -> bool:
    """Return whether both Docker sides include the remote archive security fixes."""
    try:
        client, server = inspect_docker_versions(runtime_binary)
    except DockerRemoteError:
        return False
    minimum = DockerVersion(*_MIN_DOCKER_ARCHIVE_VERSION)
    return client >= minimum and server >= minimum


def require_patched_docker_archive(runtime_binary: str = "docker") -> tuple[DockerVersion, DockerVersion]:
    """Fail closed unless Docker's client and daemon meet the archive security floor."""
    client, server = inspect_docker_versions(runtime_binary)
    minimum = DockerVersion(*_MIN_DOCKER_ARCHIVE_VERSION)
    if client < minimum or server < minimum:
        raise DockerRemoteError(
            "remote Docker archive import requires client and server >= "
            f"{minimum}; observed client {client}, server {server}"
        )
    return client, server
