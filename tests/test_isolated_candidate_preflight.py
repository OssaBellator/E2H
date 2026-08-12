from __future__ import annotations

from pathlib import Path

import pytest

import e2h.docker_capabilities as docker_capabilities
import e2h.docker_remote as docker_remote
import e2h.isolated_runner as isolated_runner
from e2h.docker_remote import DockerVersion
from e2h.isolated_runner import _run_capsule_isolated_container_candidate
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError
from e2h.workspace_archive import WorkspaceArchiveError

IMAGE = "python@sha256:" + "0" * 64


def _capsule(sandbox: ContainerSandbox) -> TaskCapsule:
    return TaskCapsule(
        id="remote-preflight",
        goal="Exercise cheap remote replay preflight ordering.",
        sandbox=sandbox,
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["never"])]),
    )


@pytest.mark.parametrize(
    ("sandbox", "message"),
    [
        (ContainerSandbox(image=IMAGE, workspace_access="read_write"), "workspace_access"),
        (ContainerSandbox(image=IMAGE, read_only_root=False), "read_only_root"),
        (ContainerSandbox(image=IMAGE, pull_policy="missing"), "pull_policy"),
    ],
)
def test_candidate_rejects_unsafe_policy_before_workspace_capture(
    tmp_path: Path,
    sandbox: ContainerSandbox,
    message: str,
) -> None:
    with pytest.raises(RunnerError, match=message):
        _run_capsule_isolated_container_candidate(
            _capsule(sandbox),
            tmp_path / "workspace-does-not-exist",
            max_workspace_bytes=1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )


def test_candidate_uses_shared_resource_gate_once_before_workspace_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_calls: list[str] = []
    monkeypatch.setattr(
        docker_remote,
        "inspect_docker_versions",
        lambda runtime: (DockerVersion(29, 7, 2), DockerVersion(29, 7, 2)),
    )
    monkeypatch.setattr(
        docker_capabilities,
        "require_docker_resource_limits",
        lambda runtime: resource_calls.append(runtime),
    )
    monkeypatch.setattr(
        isolated_runner,
        "_require_volume_free_image",
        lambda runtime, image: None,
    )

    def stop_at_capture(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise WorkspaceArchiveError("capture reached")

    monkeypatch.setattr(isolated_runner, "stable_workspace_archive", stop_at_capture)

    with pytest.raises(RunnerError, match="capture reached"):
        _run_capsule_isolated_container_candidate(
            _capsule(ContainerSandbox(image=IMAGE)),
            tmp_path / "workspace-does-not-matter",
            max_workspace_bytes=1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )

    assert resource_calls == ["docker-test"]
