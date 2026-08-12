"""Container sandbox command construction and timeout cleanup."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

from e2h.models import AllowedActions, CommandCheck, ContainerSandbox, TaskCapsule

_CONTAINER_ROOT = PurePosixPath("/workspace")
_CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")
_CLEANUP_TIMEOUT_SECONDS = 10.0
_CONTAINER_SHM_SIZE = "64m"


class SandboxError(RuntimeError):
    """Raised when a container invocation cannot be constructed safely."""


def _validated_capsule_policy(capsule: TaskCapsule) -> tuple[ContainerSandbox, AllowedActions]:
    if type(capsule) is not TaskCapsule:
        raise SandboxError(
            f"invalid task capsule: expected TaskCapsule, got {type(capsule).__name__}"
        )
    sandbox = capsule.sandbox
    allowed_actions = capsule.allowed_actions
    if sandbox is None:
        raise SandboxError("container execution requires capsule.sandbox")
    if type(sandbox) is not ContainerSandbox or type(allowed_actions) is not AllowedActions:
        raise SandboxError("invalid task capsule: sandbox policy has invalid model types")
    try:
        sandbox_payload = sandbox.model_dump(mode="python", warnings="none")
        actions_payload = allowed_actions.model_dump(mode="python", warnings="none")
        return (
            ContainerSandbox.model_validate(sandbox_payload),
            AllowedActions.model_validate(actions_payload),
        )
    except ValueError as exc:
        raise SandboxError(f"invalid task capsule: {exc}") from exc


def _validated_check(check: CommandCheck) -> CommandCheck:
    if type(check) is not CommandCheck:
        raise SandboxError(
            f"invalid command check: expected CommandCheck, got {type(check).__name__}"
        )
    try:
        payload = check.model_dump(mode="python", warnings="none")
        return CommandCheck.model_validate(payload)
    except ValueError as exc:
        raise SandboxError(f"invalid command check: {exc}") from exc


def _validated_runtime_binary(runtime_binary: str) -> str:
    if not runtime_binary or "\x00" in runtime_binary:
        raise SandboxError("container runtime binary must be non-empty and contain no NUL")
    return runtime_binary


def _container_workdir(relative_cwd: str) -> str:
    if "\x00" in relative_cwd:
        raise SandboxError("container working directory must not contain NUL")
    path = PurePosixPath(relative_cwd)
    if path.is_absolute() or ".." in path.parts:
        raise SandboxError(f"unsafe container working directory: {relative_cwd}")
    if str(path) == ".":
        return str(_CONTAINER_ROOT)
    return str(_CONTAINER_ROOT.joinpath(path))


def build_container_argv(
    capsule: TaskCapsule,
    check: CommandCheck,
    workspace_root: Path,
    relative_cwd: str,
    cidfile: Path,
    *,
    runtime_binary: str | None = None,
) -> list[str]:
    """Build a deterministic Docker invocation for one capsule check."""
    sandbox, allowed_actions = _validated_capsule_policy(capsule)
    check = _validated_check(check)
    runtime = _validated_runtime_binary(
        sandbox.engine if runtime_binary is None else runtime_binary
    )
    workspace_text = str(workspace_root)
    cidfile_text = str(cidfile)
    if "\x00" in workspace_text or "\x00" in cidfile_text:
        raise SandboxError("container filesystem arguments must not contain NUL")
    mount = f"type=bind,src={workspace_text},dst={_CONTAINER_ROOT}"
    if sandbox.workspace_access == "read_only":
        mount += ",readonly"
    argv = [
        runtime,
        "run",
        "--rm",
        "--init",
        "--log-driver",
        "none",
        "--no-healthcheck",
        "--cidfile",
        cidfile_text,
        "--pull",
        sandbox.pull_policy,
        "--hostname",
        "e2h",
        "--workdir",
        _container_workdir(relative_cwd),
        "--mount",
        mount,
        "--network",
        "none" if allowed_actions.network == "deny" else "bridge",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        str(sandbox.pids_limit),
        "--memory",
        f"{sandbox.memory_mb}m",
        "--cpus",
        f"{sandbox.cpus:g}",
        "--user",
        sandbox.user,
        "--tmpfs",
        f"/tmp:rw,nosuid,size={sandbox.tmpfs_mb}m",
        "--shm-size",
        _CONTAINER_SHM_SIZE,
        "--entrypoint",
        check.argv[0],
    ]
    if sandbox.read_only_root:
        argv.append("--read-only")
    for key, value in sorted(check.env.items()):
        argv.extend(["--env", f"{key}={value}"])
    argv.append(sandbox.image)
    argv.extend(check.argv[1:])
    return argv


def force_remove_container(runtime_binary: str, cidfile: Path) -> str | None:
    """Best-effort force removal after the attached runtime process times out."""
    try:
        runtime_binary = _validated_runtime_binary(runtime_binary)
    except SandboxError as exc:
        return str(exc)
    try:
        container_id = cidfile.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "container runtime timed out before writing a container ID"
    except (OSError, ValueError) as exc:
        return f"unable to read container ID after timeout: {exc}"
    if not _CONTAINER_ID_PATTERN.fullmatch(container_id):
        return "container runtime wrote an invalid container ID"
    try:
        completed = subprocess.run(
            [runtime_binary, "rm", "-f", container_id],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_CLEANUP_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return f"unable to force-remove timed-out container: {exc}"
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        return f"container force-removal failed with exit {completed.returncode}: {stderr}"
    return None
