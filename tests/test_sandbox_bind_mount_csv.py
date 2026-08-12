from __future__ import annotations

import csv
from pathlib import Path

import pytest

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


@pytest.mark.parametrize("name", ["workspace,segment", 'workspace"segment'])
def test_container_builder_csv_encodes_bind_mount_source(tmp_path: Path, name: str) -> None:
    workspace = tmp_path / name
    capsule = TaskCapsule(
        id="bind-mount-csv",
        goal="Keep Docker mount fields unambiguous.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )

    argv = build_container_argv(
        capsule,
        capsule.success.commands[0],
        workspace,
        ".",
        tmp_path / "cid",
        runtime_binary="docker-test",
    )

    mount = argv[argv.index("--mount") + 1]
    assert next(csv.reader([mount])) == [
        "type=bind",
        f"src={workspace}",
        "dst=/workspace",
        "readonly",
    ]


def test_container_builder_keeps_simple_mount_text_stable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    capsule = TaskCapsule(
        id="bind-mount-simple",
        goal="Keep ordinary Docker mount text stable.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )

    argv = build_container_argv(
        capsule,
        capsule.success.commands[0],
        workspace,
        ".",
        tmp_path / "cid",
        runtime_binary="docker-test",
    )

    assert argv[argv.index("--mount") + 1] == (
        f"type=bind,src={workspace},dst=/workspace,readonly"
    )
