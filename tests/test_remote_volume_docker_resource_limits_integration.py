from __future__ import annotations

import json
import os
import shutil
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
_MEMORY_MB = 64
_MEMORY_BYTES = _MEMORY_MB * 1024 * 1024
_SHM_BYTES = 64 * 1024 * 1024
_NOFILE_LIMIT = 1024


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


def _resource_probe_script() -> str:
    return """
import json
import os
import resource
from pathlib import Path

root = Path('/sys/fs/cgroup')
shm = os.statvfs('/dev/shm')
payload = {
    'core': list(resource.getrlimit(resource.RLIMIT_CORE)),
    'nofile': list(resource.getrlimit(resource.RLIMIT_NOFILE)),
    'shm_bytes': shm.f_frsize * shm.f_blocks,
}
if (root / 'memory.max').exists():
    payload['cgroup'] = 'v2'
    payload['memory_max'] = (root / 'memory.max').read_text().strip()
    payload['swap_max'] = (root / 'memory.swap.max').read_text().strip()
else:
    memory_root = root / 'memory'
    payload['cgroup'] = 'v1'
    payload['memory_max'] = (memory_root / 'memory.limit_in_bytes').read_text().strip()
    memsw = memory_root / 'memory.memsw.limit_in_bytes'
    payload['memsw_max'] = memsw.read_text().strip() if memsw.exists() else None
print(json.dumps(payload, sort_keys=True))
""".strip()


def test_real_docker_enforces_remote_memory_swap_shm_and_ulimits(tmp_path: Path) -> None:
    if not sealed_workspace_archive_supported():
        pytest.skip("real Docker resource validation requires Linux memfd sealing")
    runtime = _runtime()
    image = _image()
    require_patched_docker_archive(runtime)
    _docker_lines(runtime, ["image", "inspect", "--format", "{{.Id}}", image])

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = _resources(runtime)
    capsule = TaskCapsule(
        id="real-docker-resource-limits",
        goal="Verify remote replay memory, swap, shared-memory, and process limits.",
        sandbox=ContainerSandbox(image=image, memory_mb=_MEMORY_MB),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="limits",
                    argv=["python", "-c", _resource_probe_script()],
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
    payload = json.loads(result.checks[0].stdout)
    assert payload["core"] == [0, 0]
    assert payload["nofile"] == [_NOFILE_LIMIT, _NOFILE_LIMIT]
    assert int(payload["memory_max"]) == _MEMORY_BYTES
    assert int(payload["shm_bytes"]) == _SHM_BYTES
    if payload["cgroup"] == "v2":
        assert int(payload["swap_max"]) == 0
    else:
        assert payload["cgroup"] == "v1"
        assert payload["memsw_max"] is not None
        assert int(payload["memsw_max"]) == _MEMORY_BYTES

    after = _resources(runtime)
    assert not (after[0] - before[0])
    assert not (after[1] - before[1])
