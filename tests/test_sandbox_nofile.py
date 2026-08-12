from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def test_container_builder_pins_current_docker_nofile_default(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="bounded-nofile",
        goal="Keep file-descriptor limits independent of Docker daemon defaults.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )

    argv = build_container_argv(
        capsule,
        capsule.success.commands[0],
        tmp_path.resolve(),
        ".",
        tmp_path / "cid",
        runtime_binary="docker-test",
    )

    pairs = [
        argv[index + 1]
        for index, value in enumerate(argv[:-1])
        if value == "--ulimit"
    ]
    assert pairs == ["nofile=1024:1024"]
