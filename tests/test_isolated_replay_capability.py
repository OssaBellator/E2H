from __future__ import annotations

from pathlib import Path

import pytest

import e2h.isolated_runner as isolated_runner
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError

IMAGE = "python@sha256:" + "0" * 64


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="isolated-policy",
        goal="Exercise isolated remote replay policy.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["python", "-V"])]),
    )


@pytest.mark.parametrize("supported", [False, True])
def test_snapshot_capture_support_matches_sealed_archive_capability(
    monkeypatch: pytest.MonkeyPatch,
    supported: bool,
) -> None:
    monkeypatch.setattr(
        isolated_runner,
        "sealed_workspace_archive_supported",
        lambda: supported,
    )

    assert isolated_runner.isolated_workspace_snapshot_supported() is supported


def test_remote_container_replay_remains_fail_closed() -> None:
    assert isolated_runner.isolated_container_replay_supported() is False


def test_isolated_container_runner_refuses_mutable_host_path(tmp_path: Path) -> None:
    with pytest.raises(RunnerError, match="same-UID-mutable host pathname"):
        isolated_runner.run_capsule_isolated_container(
            _capsule(),
            tmp_path,
            max_workspace_bytes=1024,
            max_workspace_entries=10,
        )
