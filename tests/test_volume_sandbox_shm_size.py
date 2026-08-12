from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv, build_container_volume_argv

IMAGE = "python@sha256:" + "0" * 64


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="remote-bounded-shm",
        goal="Keep direct and remote /dev/shm bounds aligned.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )


def test_direct_and_retained_volume_builders_pin_same_shared_memory(
    tmp_path: Path,
) -> None:
    capsule = _capsule()
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

    assert direct[direct.index("--shm-size") + 1] == "64m"
    assert remote[remote.index("--shm-size") + 1] == "64m"
    assert direct.count("--shm-size") == 1
    assert remote.count("--shm-size") == 1
