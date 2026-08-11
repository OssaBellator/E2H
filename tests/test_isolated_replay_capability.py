from __future__ import annotations

import os
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


def test_snapshot_capture_support_requires_every_fd_primitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolated_runner, "directory_binding_supported", lambda: True)
    monkeypatch.setattr(
        isolated_runner.os,
        "supports_dir_fd",
        {os.stat, os.readlink},
    )
    monkeypatch.setattr(isolated_runner.os, "supports_fd", {os.listdir})

    assert isolated_runner.isolated_workspace_snapshot_supported() is True

    monkeypatch.setattr(isolated_runner.os, "supports_fd", set())
    assert isolated_runner.isolated_workspace_snapshot_supported() is False


def test_snapshot_capture_support_requires_directory_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(isolated_runner, "directory_binding_supported", lambda: False)
    monkeypatch.setattr(
        isolated_runner.os,
        "supports_dir_fd",
        {os.stat, os.readlink},
    )
    monkeypatch.setattr(isolated_runner.os, "supports_fd", {os.listdir})

    assert isolated_runner.isolated_workspace_snapshot_supported() is False


def test_remote_container_replay_remains_fail_closed() -> None:
    assert isolated_runner.isolated_container_replay_supported() is False


def test_isolated_container_runner_waits_for_real_runtime_validation(tmp_path: Path) -> None:
    with pytest.raises(RunnerError, match="real patched Docker runtime"):
        isolated_runner.run_capsule_isolated_container(
            _capsule(),
            tmp_path,
            max_workspace_bytes=1024,
            max_workspace_entries=10,
        )
