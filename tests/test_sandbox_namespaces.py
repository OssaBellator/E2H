from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def test_container_builder_pins_private_cgroup_and_ipc_namespaces(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="private-namespaces",
        goal="Keep replay namespace isolation independent of Docker daemon defaults.",
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

    assert argv.count("--cgroupns") == 1
    assert argv[argv.index("--cgroupns") + 1] == "private"
    assert argv.count("--ipc") == 1
    assert argv[argv.index("--ipc") + 1] == "private"
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--pids-limit") + 1] == str(capsule.sandbox.pids_limit)
