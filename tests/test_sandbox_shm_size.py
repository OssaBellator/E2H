from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def test_container_builder_pins_shared_memory_size(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="bounded-shm",
        goal="Make /dev/shm independent of Docker daemon defaults.",
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

    assert argv.count("--shm-size") == 1
    assert argv[argv.index("--shm-size") + 1] == "64m"
    assert argv[argv.index("--memory") + 1] == "512m"
    assert argv[argv.index("--tmpfs") + 1] == "/tmp:rw,nosuid,size=64m"
