from __future__ import annotations

from pathlib import Path

import pytest

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import SandboxError, build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def _capsule(*, workspace_access: str = "read_only") -> TaskCapsule:
    return TaskCapsule(
        id="bound-sandbox",
        goal="Build one bound container invocation.",
        sandbox=ContainerSandbox(image=IMAGE, workspace_access=workspace_access),
        success=SuccessSpec(
            commands=[CommandCheck(id="check", argv=["python", "-V"])]
        ),
    )


def _mounts(argv: list[str]) -> list[str]:
    return [argv[index + 1] for index, value in enumerate(argv) if value == "--mount"]


def test_builder_uses_bound_workspace_and_exact_workdir_mounts(tmp_path: Path) -> None:
    capsule = _capsule()
    argv = build_container_argv(
        capsule,
        capsule.success.commands[0],
        tmp_path.resolve(),
        "task/nested",
        tmp_path / "cid",
        runtime_binary="docker-test",
        workspace_mount_source="/proc/100/fd/4",
        working_directory_mount_source="/proc/100/fd/5",
    )

    assert _mounts(argv) == [
        "type=bind,src=/proc/100/fd/4,dst=/workspace,readonly",
        "type=bind,src=/proc/100/fd/5,dst=/workspace/task/nested,readonly",
    ]
    assert argv[argv.index("--workdir") + 1] == "/workspace/task/nested"


def test_builder_omits_redundant_exact_mount_for_workspace_root(tmp_path: Path) -> None:
    capsule = _capsule(workspace_access="read_write")
    argv = build_container_argv(
        capsule,
        capsule.success.commands[0],
        tmp_path.resolve(),
        ".",
        tmp_path / "cid",
        workspace_mount_source="/proc/100/fd/4",
        working_directory_mount_source="/proc/100/fd/4",
    )

    assert _mounts(argv) == ["type=bind,src=/proc/100/fd/4,dst=/workspace"]


def test_builder_rejects_relative_bound_mount_source(tmp_path: Path) -> None:
    capsule = _capsule()

    with pytest.raises(SandboxError, match="workspace mount source must be absolute"):
        build_container_argv(
            capsule,
            capsule.success.commands[0],
            tmp_path.resolve(),
            ".",
            tmp_path / "cid",
            workspace_mount_source="relative/fd/4",
        )
