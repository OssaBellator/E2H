from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from e2h.docker_remote import require_patched_docker_archive
from e2h.isolated_runner import _run_capsule_isolated_container_candidate
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, RunStatus
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
    replay_containers = frozenset(name for name in names if name.startswith("e2h-replay-"))
    return volumes, replay_containers


def _metadata_probe_script() -> str:
    return """
import json
import os
import stat
from pathlib import Path

path = Path('marker.txt')
info = os.stat(path, follow_symlinks=False)
print(json.dumps({
    'content': path.read_text(),
    'uid': info.st_uid,
    'gid': info.st_gid,
    'mode': stat.S_IMODE(info.st_mode),
}, sort_keys=True))
""".strip()


def test_real_docker_import_preserves_workspace_ownership_and_mode(tmp_path: Path) -> None:
    if not sealed_workspace_archive_supported():
        pytest.skip("real Docker metadata validation requires Linux memfd sealing")
    runtime = _runtime()
    image = _image()
    require_patched_docker_archive(runtime)
    _docker_lines(runtime, ["image", "inspect", "--format", "{{.Id}}", image])

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "marker.txt"
    marker.write_text("trusted", encoding="utf-8")
    marker.chmod(0o4755)
    source = marker.stat(follow_symlinks=False)
    expected = {
        "content": "trusted",
        "uid": source.st_uid,
        "gid": source.st_gid,
        "mode": stat.S_IMODE(source.st_mode),
    }
    assert expected["mode"] == 0o4755

    before = _resources(runtime)
    capsule = TaskCapsule(
        id="real-docker-workspace-metadata",
        goal="Verify Docker import preserves sealed workspace metadata.",
        sandbox=ContainerSandbox(image=image),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="metadata",
                    argv=["python", "-c", _metadata_probe_script()],
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

    assert result.status is RunStatus.PASSED
    assert result.checks[0].status is CheckStatus.PASSED
    assert json.loads(result.checks[0].stdout) == expected

    after = _resources(runtime)
    assert not (after[0] - before[0])
    assert not (after[1] - before[1])
