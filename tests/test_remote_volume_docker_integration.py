from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from e2h.docker_remote import require_patched_docker_archive
from e2h.isolated_runner import _run_capsule_isolated_container_candidate
from e2h.models import CommandCheck, ContainerSandbox, InitialState, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, RunStatus
from e2h.workspace_archive import sealed_workspace_archive_supported

IMAGE_ENV = "E2H_DOCKER_TEST_PYTHON_IMAGE"
RUNTIME_ENV = "E2H_DOCKER_TEST_RUNTIME"


@dataclass(frozen=True)
class _DaemonResources:
    volumes: frozenset[str]
    replay_containers: frozenset[str]


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


def _resources(runtime: str) -> _DaemonResources:
    volumes = _docker_lines(
        runtime,
        ["volume", "ls", "-q", "--filter", "label=e2h.remote-replay=workspace"],
    )
    names = _docker_lines(
        runtime,
        ["ps", "-a", "--format", "{{.Names}}", "--filter", "name=e2h-replay-check-"],
    )
    return _DaemonResources(
        volumes=volumes,
        replay_containers=frozenset(
            name for name in names if name.startswith("e2h-replay-check-")
        ),
    )


def _assert_no_new_resources(before: _DaemonResources, after: _DaemonResources) -> None:
    assert not (after.volumes - before.volumes)
    assert not (after.replay_containers - before.replay_containers)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    task = workspace / "task"
    nested = workspace / "shared" / "nested"
    task.mkdir(parents=True)
    nested.mkdir(parents=True)
    nested.chmod(0o777)
    (nested / "marker.txt").write_text("trusted", encoding="utf-8")
    link = task / "link"
    try:
        link.symlink_to("../shared")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    return workspace


def _capsule(image: str, commands: list[CommandCheck]) -> TaskCapsule:
    return TaskCapsule(
        id="real-docker-sealed-volume",
        goal="Validate the sealed-volume remote replay candidate against Docker.",
        initial_state=InitialState(working_directory="task"),
        sandbox=ContainerSandbox(image=image),
        success=SuccessSpec(commands=commands),
    )


def _preflight() -> tuple[str, str]:
    if not sealed_workspace_archive_supported():
        pytest.skip("real Docker candidate validation requires Linux memfd sealing")
    runtime = _runtime()
    image = _image()
    require_patched_docker_archive(runtime)
    _docker_lines(runtime, ["image", "inspect", "--format", "{{.Id}}", image])
    return runtime, image


def test_real_docker_volume_import_cwd_and_read_only_workspace(tmp_path: Path) -> None:
    runtime, image = _preflight()
    workspace = _workspace(tmp_path)
    before = _resources(runtime)
    capsule = _capsule(
        image,
        [
            CommandCheck(
                id="read",
                cwd="link/nested",
                argv=[
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "assert Path('marker.txt').read_text() == 'trusted'; "
                        "print('read-ok')"
                    ),
                ],
            ),
            CommandCheck(
                id="readonly",
                cwd="link/nested",
                argv=[
                    "python",
                    "-c",
                    (
                        "from pathlib import Path; "
                        "p=Path('blocked.txt'); "
                        "\ntry: p.write_text('x')"
                        "\nexcept OSError: print('readonly-ok')"
                        "\nelse: raise SystemExit('workspace unexpectedly writable')"
                    ),
                ],
            ),
        ],
    )

    result = _run_capsule_isolated_container_candidate(
        capsule,
        workspace.resolve(),
        max_workspace_bytes=1024 * 1024,
        max_workspace_entries=100,
        container_runtime=runtime,
    )

    assert result.status is RunStatus.PASSED
    assert [check.status for check in result.checks] == [
        CheckStatus.PASSED,
        CheckStatus.PASSED,
    ]
    assert result.checks[0].cwd == "shared/nested"
    assert result.checks[0].stdout == "read-ok\n"
    assert result.checks[1].stdout == "readonly-ok\n"
    assert not (workspace / "shared" / "nested" / "blocked.txt").exists()
    _assert_no_new_resources(before, _resources(runtime))


def test_real_docker_command_failure_still_cleans_volume(tmp_path: Path) -> None:
    runtime, image = _preflight()
    workspace = _workspace(tmp_path)
    before = _resources(runtime)
    capsule = _capsule(
        image,
        [
            CommandCheck(
                id="fail",
                cwd="link/nested",
                argv=["python", "-c", "raise SystemExit(7)"],
            )
        ],
    )

    result = _run_capsule_isolated_container_candidate(
        capsule,
        workspace.resolve(),
        max_workspace_bytes=1024 * 1024,
        max_workspace_entries=100,
        container_runtime=runtime,
    )

    assert result.status is RunStatus.FAILED
    assert result.checks[0].status is CheckStatus.FAILED
    assert result.checks[0].exit_code == 7
    _assert_no_new_resources(before, _resources(runtime))


def test_real_docker_timeout_removes_check_container_and_volume(tmp_path: Path) -> None:
    runtime, image = _preflight()
    workspace = _workspace(tmp_path)
    before = _resources(runtime)
    capsule = _capsule(
        image,
        [
            CommandCheck(
                id="timeout",
                cwd="link/nested",
                argv=["python", "-c", "import time; time.sleep(30)"],
                timeout_seconds=0.5,
            )
        ],
    )

    result = _run_capsule_isolated_container_candidate(
        capsule,
        workspace.resolve(),
        max_workspace_bytes=1024 * 1024,
        max_workspace_entries=100,
        container_runtime=runtime,
    )

    assert result.status is RunStatus.FAILED
    assert result.checks[0].status is CheckStatus.TIMED_OUT
    _assert_no_new_resources(before, _resources(runtime))
