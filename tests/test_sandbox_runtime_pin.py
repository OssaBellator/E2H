from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def test_container_builder_pins_runc_runtime(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="pinned-runc",
        goal="Keep OCI runtime selection independent of Docker daemon defaults.",
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

    assert argv.count("--runtime") == 1
    assert argv[argv.index("--runtime") + 1] == "runc"
    assert argv.count("--security-opt") == 2
    assert "seccomp=builtin" in argv
