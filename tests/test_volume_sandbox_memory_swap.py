from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv, build_container_volume_argv

IMAGE = "python@sha256:" + "0" * 64


def test_retained_volume_memory_and_swap_follow_custom_capsule_limit(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="remote-bounded-memory-swap",
        goal="Keep direct and remote memory+swap ceilings aligned.",
        sandbox=ContainerSandbox(image=IMAGE, memory_mb=256),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )
    check = capsule.success.commands[0]
    direct = build_container_argv(
        capsule,
        check,
        tmp_path.resolve(),
        ".",
        tmp_path / "cid",
        runtime_binary="docker-test",
    )
    remote = build_container_volume_argv(
        capsule,
        check,
        "e2h-replay-workspace-abc",
        ".",
        "e2h-replay-check-def",
        runtime_binary="docker-test",
    )

    for argv in (direct, remote):
        assert argv[argv.index("--memory") + 1] == "256m"
        assert argv[argv.index("--memory-swap") + 1] == "256m"
        assert argv.count("--memory-swap") == 1
