from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def test_container_builder_pins_builtin_seccomp_profile() -> None:
    capsule = TaskCapsule(
        id="builtin-seccomp",
        goal="Do not inherit a daemon-specific seccomp default.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["python", "-V"])]),
    )

    argv = build_container_argv(
        capsule,
        capsule.success.commands[0],
        Path("/tmp/workspace"),
        ".",
        Path("/tmp/container.cid"),
        runtime_binary="docker-test",
    )

    security_opts = [
        argv[index + 1]
        for index, value in enumerate(argv[:-1])
        if value == "--security-opt"
    ]
    assert security_opts == ["no-new-privileges:true", "seccomp=builtin"]
