"""Docker-managed workspace helpers for future remote container replay."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - imported only on non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

from e2h.models import ContainerSandbox
from e2h.workspace_archive import WorkspaceArchive

_MIN_DOCKER_ARCHIVE_VERSION = (29, 7, 2)
_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")
_RESOURCE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
_VERSION_TIMEOUT_SECONDS = 10.0
_CONTROL_TIMEOUT_SECONDS = 30.0
_MAX_ERROR_CHARS = 4096
_WORKSPACE_ROOT = "/workspace"


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


def _validated_remote_sandbox(sandbox: ContainerSandbox) -> ContainerSandbox:
    if type(sandbox) is not ContainerSandbox:
        raise DockerRemoteError(
            f"invalid container sandbox: expected ContainerSandbox, got {type(sandbox).__name__}"
        )
    try:
        validated = ContainerSandbox.model_validate(
            sandbox.model_dump(mode="python", warnings="none")
        )
    except ValueError as exc:
        raise DockerRemoteError(f"invalid container sandbox: {exc}") from exc
    if validated.workspace_access != "read_only":
        raise DockerRemoteError("remote Docker replay requires workspace_access='read_only'")
    if not validated.read_only_root:
        raise DockerRemoteError("remote Docker replay requires read_only_root=true")
    if validated.pull_policy != "never":
        raise DockerRemoteError("remote Docker replay requires pull_policy='never'")
    return validated


def _required_archive_seals() -> int:
    if fcntl is None:
        raise DockerRemoteError("sealed workspace archives require Linux file seals")
    names = ("F_SEAL_WRITE", "F_SEAL_GROW", "F_SEAL_SHRINK", "F_SEAL_SEAL")
    values = [getattr(fcntl, name, None) for name in names]
    if any(value is None for value in values):
        raise DockerRemoteError("sealed workspace archives require Linux file seals")
    return sum(int(value) for value in values if value is not None)


def _validated_workspace_archive(archive: WorkspaceArchive) -> WorkspaceArchive:
    if type(archive) is not WorkspaceArchive:
        raise DockerRemoteError(
            f"invalid workspace archive: expected WorkspaceArchive, got {type(archive).__name__}"
        )
    if archive.source_bytes < 0 or archive.entries < 0 or archive.archive_bytes < 1:
        raise DockerRemoteError("workspace archive metadata is invalid")
    required = _required_archive_seals()
    if fcntl is None or not hasattr(fcntl, "F_GET_SEALS"):
        raise DockerRemoteError("sealed workspace archives require Linux file seals")
    try:
        descriptor = archive.file.fileno()
        observed = fcntl.fcntl(descriptor, fcntl.F_GET_SEALS)
        current_size = os.fstat(descriptor).st_size
    except (OSError, ValueError) as exc:
        raise DockerRemoteError(f"unable to verify sealed workspace archive: {exc}") from exc
    if observed & required != required:
        raise DockerRemoteError("workspace archive is not sealed against mutation")
    if current_size != archive.archive_bytes:
        raise DockerRemoteError("workspace archive size does not match captured metadata")
    return archive


def _render_error(value: bytes) -> str:
    rendered = value.decode("utf-8", errors="replace").strip()
    if len(rendered) <= _MAX_ERROR_CHARS:
        return rendered
    return rendered[: _MAX_ERROR_CHARS - 3] + "..."


def _run_docker(
    runtime: str,
    args: list[str],
    *,
    stdin: BinaryIO | None = None,
    timeout: float = _CONTROL_TIMEOUT_SECONDS,
) -> str:
    if stdin is not None:
        stdin.seek(0)
    try:
        completed = subprocess.run(
            [runtime, *args],
            stdin=subprocess.DEVNULL if stdin is None else stdin,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise DockerRemoteError(f"Docker control command failed to complete: {exc}") from exc
    if completed.returncode != 0:
        error = _render_error(completed.stderr) or _render_error(completed.stdout)
        if not error:
            error = "unknown Docker error"
        raise DockerRemoteError(
            f"Docker control command failed with exit {completed.returncode}: {error}"
        )
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _best_effort_docker(runtime: str, args: list[str]) -> str | None:
    try:
        _run_docker(runtime, args)
    except DockerRemoteError as exc:
        return str(exc)
    return None


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


def require_patched_docker_archive(
    runtime_binary: str = "docker",
) -> tuple[DockerVersion, DockerVersion]:
    """Fail closed unless Docker's client and daemon meet the archive security floor."""
    client, server = inspect_docker_versions(runtime_binary)
    minimum = DockerVersion(*_MIN_DOCKER_ARCHIVE_VERSION)
    if client < minimum or server < minimum:
        raise DockerRemoteError(
            "remote Docker archive import requires client and server >= "
            f"{minimum}; observed client {client}, server {server}"
        )
    return client, server


def _resource_name(noun: str) -> str:
    value = f"e2h-replay-{noun}-{secrets.token_hex(16)}"
    if _RESOURCE_PATTERN.fullmatch(value) is None:
        raise DockerRemoteError(f"generated invalid Docker {noun} name")
    return value


@contextmanager
def prepared_workspace_volume(
    sandbox: ContainerSandbox,
    archive: WorkspaceArchive,
    *,
    runtime_binary: str = "docker",
) -> Iterator[str]:
    """Populate one fresh Docker-managed volume from a sealed workspace archive."""
    runtime = _validated_runtime_binary(runtime_binary)
    policy = _validated_remote_sandbox(sandbox)
    verified_archive = _validated_workspace_archive(archive)
    require_patched_docker_archive(runtime)

    volume_name = _resource_name("workspace")
    container_name = _resource_name("prepare")
    volume_may_exist = False
    container_may_exist = False
    primary_error: BaseException | None = None

    try:
        # A create command can time out or lose its response after the daemon has
        # already created the named resource. Arm cleanup before each create so
        # an ambiguous client-side failure cannot leak daemon-side state.
        volume_may_exist = True
        created_volume = _run_docker(
            runtime,
            [
                "volume",
                "create",
                "--label",
                "e2h.remote-replay=workspace",
                volume_name,
            ],
        )
        if created_volume != volume_name:
            raise DockerRemoteError("Docker created an unexpected workspace volume")

        mount = f"type=volume,src={volume_name},dst={_WORKSPACE_ROOT},volume-nocopy"
        container_may_exist = True
        created_container = _run_docker(
            runtime,
            [
                "create",
                "--name",
                container_name,
                "--pull",
                "never",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--log-driver",
                "none",
                "--mount",
                mount,
                "--entrypoint",
                "",
                policy.image,
                "__e2h_workspace_preparation_container_is_never_started__",
            ],
        )
        if not created_container:
            raise DockerRemoteError("Docker did not return a preparation container ID")

        _run_docker(
            runtime,
            [
                "cp",
                "--archive",
                "--quiet",
                "-",
                f"{container_name}:{_WORKSPACE_ROOT}",
            ],
            stdin=verified_archive.file,
        )

        _run_docker(runtime, ["rm", "-f", container_name])
        container_may_exist = False
        yield volume_name
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[str] = []
        if container_may_exist:
            error = _best_effort_docker(runtime, ["rm", "-f", container_name])
            if error is not None:
                cleanup_errors.append(error)
        if volume_may_exist:
            error = _best_effort_docker(runtime, ["volume", "rm", "-f", volume_name])
            if error is not None:
                cleanup_errors.append(error)
        if primary_error is None and cleanup_errors:
            raise DockerRemoteError(
                "Docker workspace cleanup failed: " + "; ".join(cleanup_errors)
            )
