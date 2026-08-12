from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from e2h.docker_remote import require_patched_docker_archive
from e2h.isolated_runner import _run_capsule_isolated_container_candidate
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError
from e2h.workspace_archive import sealed_workspace_archive_supported

IMAGE_ENV = "E2H_DOCKER_TEST_PYTHON_IMAGE"
RUNTIME_ENV = "E2H_DOCKER_TEST_RUNTIME"


def _runtime() -> str:
    runtime = os.environ.get(RUNTIME_ENV, "docker")
    if shutil.which(runtime) is None:
        pytest.skip(f"{runtime!r} is not available")
    return runtime


def _image() -> str:
    image = os.environ.get(IMAGE_ENV)
    if image is None:
        pytest.skip(
            f"set {IMAGE_ENV} to a pre-pulled immutable Python image digest for real Docker tests"
        )
    return image


def _docker_lines(runtime: str, args: list[str]) -> frozenset[str]:
    completed = subprocess.run(
        [runtime, *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10.0,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(
            f"Docker {' '.join(args)} failed with exit {completed.returncode}: "
            f"{completed.stderr.strip()}"
        )
    return frozenset(line.strip() for line in completed.stdout.splitlines() if line.strip())


def _resources(runtime: str) -> tuple[frozenset[str], frozenset[str]]:
    volumes = _docker_lines(
        runtime,
        ["volume", "ls", "-q", "--filter", "label=e2h.remote-replay=workspace"],
    )
    names = _docker_lines(
        runtime,
        ["ps", "-a", "--format", "{{.Names}}", "--filter", "name=e2h-replay-"],
    )
    return volumes, frozenset(name for name in names if name.startswith("e2h-replay-"))


def _preflight() -> tuple[str, str]:
    if not sealed_workspace_archive_supported():
        pytest.skip("real Docker signal validation requires Linux memfd sealing")
    runtime = _runtime()
    image = _image()
    require_patched_docker_archive(runtime)
    _docker_lines(runtime, ["image", "inspect", "--format", "{{.Id}}", image])
    return runtime, image


def _capsule(image: str, program: str) -> TaskCapsule:
    return TaskCapsule(
        id="real-docker-signal-exit-policy",
        goal="Verify Docker signal-encoded statuses remain fail-closed.",
        sandbox=ContainerSandbox(image=image),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="ambiguous-exit",
                    argv=["python", "-c", program],
                )
            ]
        ),
    )


@pytest.mark.parametrize(
    "program",
    [
        "import os, signal; os.kill(os.getpid(), signal.SIGKILL)",
        "import sys; sys.exit(137)",
    ],
)
def test_real_docker_rejects_signal_ambiguous_137_task_verdict(
    tmp_path: Path,
    program: str,
) -> None:
    runtime, image = _preflight()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = _resources(runtime)

    with pytest.raises(RunnerError, match=r"signal-ambiguous Docker exit status.*137"):
        _run_capsule_isolated_container_candidate(
            _capsule(image, program),
            workspace.resolve(),
            max_workspace_bytes=1024 * 1024,
            max_workspace_entries=100,
            container_runtime=runtime,
        )

    after = _resources(runtime)
    assert not (after[0] - before[0])
    assert not (after[1] - before[1])
