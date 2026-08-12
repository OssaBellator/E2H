from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from e2h.docker_remote import require_patched_docker_archive
from e2h.failures import FailureCode
from e2h.isolated_runner import _run_capsule_isolated_container_candidate
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, RunStatus
from e2h.workspace_archive import sealed_workspace_archive_supported

IMAGE_ENV = "E2H_DOCKER_TEST_PYTHON_IMAGE"
RUNTIME_ENV = "E2H_DOCKER_TEST_RUNTIME"


def _preflight() -> tuple[str, str]:
    if not sealed_workspace_archive_supported():
        pytest.skip("real Docker OOM validation requires Linux memfd sealing")
    runtime = os.environ.get(RUNTIME_ENV, "docker")
    if shutil.which(runtime) is None:
        pytest.skip(f"{runtime!r} is not available")
    image = os.environ.get(IMAGE_ENV)
    if image is None:
        pytest.skip(
            f"set {IMAGE_ENV} to a pre-pulled immutable Python image digest for real Docker tests"
        )
    require_patched_docker_archive(runtime)
    completed = subprocess.run(
        [runtime, "image", "inspect", "--format", "{{.Id}}", image],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"Docker image inspect failed: {completed.stderr.strip()}")
    return runtime, image


def _resource_snapshot(runtime: str) -> tuple[frozenset[str], frozenset[str]]:
    volumes = subprocess.run(
        [
            runtime,
            "volume",
            "ls",
            "-q",
            "--filter",
            "label=e2h.remote-replay=workspace",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    containers = subprocess.run(
        [
            runtime,
            "ps",
            "-a",
            "--format",
            "{{.Names}}",
            "--filter",
            "name=e2h-replay-",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    if volumes.returncode != 0 or containers.returncode != 0:
        pytest.fail("unable to snapshot Docker replay resources")
    return (
        frozenset(line for line in volumes.stdout.splitlines() if line),
        frozenset(
            line
            for line in containers.stdout.splitlines()
            if line.startswith("e2h-replay-")
        ),
    )


def test_real_docker_oom_kill_cannot_pass_as_expected_137(tmp_path: Path) -> None:
    runtime, image = _preflight()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = _resource_snapshot(runtime)
    capsule = TaskCapsule(
        id="real-docker-oom-state",
        goal="Require Docker OOM state to override expected exit 137.",
        sandbox=ContainerSandbox(image=image, memory_mb=64),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="oom",
                    argv=[
                        "python",
                        "-c",
                        (
                            "chunks=[]\n"
                            "while True:\n"
                            "    chunks.append(bytearray(8 * 1024 * 1024))"
                        ),
                    ],
                    timeout_seconds=15.0,
                    expected_exit_codes={137},
                )
            ]
        ),
    )

    result = _run_capsule_isolated_container_candidate(
        capsule,
        workspace.resolve(),
        max_workspace_bytes=1024 * 1024,
        max_workspace_entries=100,
        container_runtime=runtime,
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].exit_code == 137
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_RUNTIME
    assert "OOM-killed" in (result.checks[0].error or "")
    assert _resource_snapshot(runtime) == before
