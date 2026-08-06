"""Container sandbox command construction and timeout cleanup."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

from e2h.models import CommandCheck, TaskCapsule

_CONTAINER_ROOT = PurePosixPath("/workspace")
_CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")
_CLEANUP_TIMEOUT_SECONDS = 10.0


class SandboxError(RuntimeError):
    """Raised when a container invocation cannot be constructed safely."""


def _container_workdir(relative_cwd: str) -> str:
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
    sandbox = capsule.sandbox
    if sandbox is None:
        raise SandboxError("container execution requires capsule.sandbox")
    runtime = runtime_binary or sandbox.engine
    mount = f"type=bind,src={workspace_root},dst={_CONTAINER_ROOT}"
    if sandbox.workspace_access == "read_only":
        mount += ",readonly"
    argv = [
        runtime,
        "run",
        "--rm",
        "--init",
        "--cidfile",
        str(cidfile),
        "--pull",
        sandbox.pull_policy,
        "--hostname",
        "e2h",
        "--workdir",
        _container_workdir(relative_cwd),
        "--mount",
        mount,
        "--network",
        "none" if capsule.allowed_actions.network == "deny" else "bridge",
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
    ]
    if sandbox.read_only_root:
        argv.append("--read-only")
    for key, value in sorted(check.env.items()):
        argv.extend(["--env", f"{key}={value}"])
    argv.append(sandbox.image)
    argv.extend(check.argv)
    return argv


def force_remove_container(runtime_binary: str, cidfile: Path) -> str | None:
    """Best-effort force removal after the attached runtime process times out."""
    try:
        container_id = cidfile.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return "container runtime timed out before writing a container ID"
    except OSError as exc:
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
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unable to force-remove timed-out container: {exc}"
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        return f"container force-removal failed with exit {completed.returncode}: {stderr}"
    return None
