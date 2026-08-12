from __future__ import annotations

from pathlib import Path

import pytest

from e2h.isolated_runner import _run_capsule_isolated_container_candidate
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError

IMAGE = "python@sha256:" + "0" * 64


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
    capsule = TaskCapsule(
        id="unsafe-remote-policy",
        goal="Reject unsafe remote policy before workspace capture.",
        sandbox=sandbox,
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["never"])]),
    )

    with pytest.raises(RunnerError, match=message):
        _run_capsule_isolated_container_candidate(
            capsule,
            tmp_path / "workspace-does-not-exist",
            max_workspace_bytes=1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )
