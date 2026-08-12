from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def test_container_builder_pins_writable_memory_resources(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="bounded-memory",
        goal="Keep container writable memory resources independent of Docker daemon defaults.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )
    assert capsule.sandbox is not None

    argv = build_container_argv(
        capsule,
        capsule.success.commands[0],
        tmp_path.resolve(),
        ".",
        tmp_path / "cid",
        runtime_binary="docker-test",
    )

    memory_limit = f"{capsule.sandbox.memory_mb}m"
    assert argv.count("--memory") == 1
    assert argv[argv.index("--memory") + 1] == memory_limit
    assert argv.count("--memory-swap") == 1
    assert argv[argv.index("--memory-swap") + 1] == memory_limit
    assert argv.count("--shm-size") == 1
    assert argv[argv.index("--shm-size") + 1] == "64m"
    assert argv[argv.index("--tmpfs") + 1] == (
        f"/tmp:rw,nosuid,mode=1777,size={capsule.sandbox.tmpfs_mb}m"
    )
