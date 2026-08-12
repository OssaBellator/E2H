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
NONROOT_IMAGE_ENV = "E2H_DOCKER_TEST_NONROOT_PYTHON_IMAGE"
RUNTIME_ENV = "E2H_DOCKER_TEST_RUNTIME"
_MTIME_NS = 1_700_000_000_123_456_789


def _runtime() -> str:
    runtime = os.environ.get(RUNTIME_ENV, "docker")
    if shutil.which(runtime) is None:
        pytest.skip(f"{runtime!r} is not available")
    return runtime


def _image(env_name: str) -> str:
    image = os.environ.get(env_name)
    if image is None:
        pytest.skip(
            f"set {env_name} to a pre-pulled immutable Python image digest for real Docker tests"
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


def metadata(path: Path):
    info = os.stat(path, follow_symlinks=False)
    return {
        'uid': info.st_uid,
        'gid': info.st_gid,
        'mode': stat.S_IMODE(info.st_mode),
        'mtime_ns': info.st_mtime_ns,
    }

marker = Path('marker.txt')
payload = {
    'root': metadata(Path('.')),
    'nested': metadata(Path('nested')),
    'marker': {
        **metadata(marker),
        'content': marker.read_text(),
    },
}
print(json.dumps(payload, sort_keys=True))
""".strip()


def _expected_metadata(path: Path) -> dict[str, int]:
    info = path.stat(follow_symlinks=False)
    return {
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": stat.S_IMODE(info.st_mode),
        "mtime_ns": info.st_mtime_ns,
    }


@pytest.mark.parametrize("image_env", [IMAGE_ENV, NONROOT_IMAGE_ENV])
def test_real_docker_import_preserves_workspace_metadata(
    tmp_path: Path,
    image_env: str,
) -> None:
    if not sealed_workspace_archive_supported():
        pytest.skip("real Docker metadata validation requires Linux memfd sealing")
    runtime = _runtime()
    image = _image(image_env)
    require_patched_docker_archive(runtime)
    _docker_lines(runtime, ["image", "inspect", "--format", "{{.Id}}", image])

    configured_identity: tuple[int, int] | None = None
    if image_env == NONROOT_IMAGE_ENV:
        configured_users = _docker_lines(
            runtime,
            ["image", "inspect", "--format", "{{.Config.User}}", image],
        )
        if len(configured_users) != 1:
            pytest.fail(
                f"{NONROOT_IMAGE_ENV} must declare an explicit numeric non-root USER uid:gid"
            )
        configured_user = next(iter(configured_users))
        parts = configured_user.split(":")
        if (
            len(parts) != 2
            or any(not part.isdigit() for part in parts)
            or int(parts[0]) == 0
            or int(parts[1]) == 0
        ):
            pytest.fail(
                f"{NONROOT_IMAGE_ENV} must declare an explicit numeric non-root USER uid:gid"
            )
        configured_identity = (int(parts[0]), int(parts[1]))

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace.chmod(0o1755)
    nested = workspace / "nested"
    nested.mkdir()
    nested.chmod(0o2755)
    marker = workspace / "marker.txt"
    marker.write_text("trusted", encoding="utf-8")
    marker.chmod(0o4755)
    for path in (marker, nested, workspace):
        os.utime(path, ns=(_MTIME_NS, _MTIME_NS), follow_symlinks=False)
    expected = {
        "root": _expected_metadata(workspace),
        "nested": _expected_metadata(nested),
        "marker": {
            **_expected_metadata(marker),
            "content": "trusted",
        },
    }
    if any(expected[key]["mtime_ns"] != _MTIME_NS for key in ("root", "nested", "marker")):
        pytest.skip("test filesystem cannot preserve the requested nanosecond mtime")
    assert _MTIME_NS % 1_000_000_000 != 0
    assert expected["root"]["mode"] == 0o1755
    assert expected["nested"]["mode"] == 0o2755
    assert expected["marker"]["mode"] == 0o4755
    if configured_identity is not None:
        source_identity = (expected["marker"]["uid"], expected["marker"]["gid"])
        if configured_identity == source_identity:
            pytest.fail(
                f"{NONROOT_IMAGE_ENV} USER must differ from the source workspace uid:gid"
            )

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
