from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def test_container_builder_pins_tmpfs_mode_and_size(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="tmpfs-mode",
        goal="Pin writable tmpfs semantics.",
        sandbox=ContainerSandbox(image=IMAGE, tmpfs_mb=23),
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

    assert argv[argv.index("--tmpfs") + 1] == "/tmp:rw,nosuid,mode=1777,size=23m"
