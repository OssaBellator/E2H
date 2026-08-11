from __future__ import annotations

import os

import pytest

import e2h.isolated_runner as isolated_runner
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError

IMAGE = "python@sha256:" + "0" * 64


def _capsule(**sandbox_overrides: object) -> TaskCapsule:
    sandbox = {"image": IMAGE, **sandbox_overrides}
    return TaskCapsule(
        id="isolated-policy",
        goal="Exercise isolated remote replay policy.",
        sandbox=ContainerSandbox.model_validate(sandbox),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["python", "-V"])]),
    )


def test_isolated_replay_support_requires_every_snapshot_fd_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolated_runner, "directory_binding_supported", lambda: True)
    monkeypatch.setattr(
        isolated_runner.os,
        "supports_dir_fd",
        {os.stat, os.readlink},
    )
    monkeypatch.setattr(isolated_runner.os, "supports_fd", {os.listdir})

    assert isolated_runner.isolated_container_replay_supported() is True

    monkeypatch.setattr(isolated_runner.os, "supports_fd", set())
    assert isolated_runner.isolated_container_replay_supported() is False


def test_isolated_replay_support_requires_directory_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolated_runner, "directory_binding_supported", lambda: False)
    monkeypatch.setattr(
        isolated_runner.os,
        "supports_dir_fd",
        {os.stat, os.readlink},
    )
    monkeypatch.setattr(isolated_runner.os, "supports_fd", {os.listdir})

    assert isolated_runner.isolated_container_replay_supported() is False


@pytest.mark.parametrize(
    ("sandbox_overrides", "message"),
    [
        ({"workspace_access": "read_write"}, "workspace_access='read_only'"),
        ({"read_only_root": False}, "read_only_root=true"),
        ({"pull_policy": "missing"}, "pull_policy='never'"),
    ],
)
def test_isolated_remote_policy_rejects_unbounded_container_settings(
    sandbox_overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RunnerError, match=message):
        isolated_runner._validate_isolated_remote_policy(_capsule(**sandbox_overrides))


def test_isolated_remote_policy_accepts_bounded_defaults() -> None:
    isolated_runner._validate_isolated_remote_policy(_capsule())
