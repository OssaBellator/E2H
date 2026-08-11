from __future__ import annotations

from pathlib import Path

from e2h.models import AllowedActions, CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv, build_container_volume_argv

IMAGE = "python@sha256:" + "0" * 64


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="remote-volume-parity",
        goal="Keep remote volume sandbox controls aligned with direct container execution.",
        allowed_actions=AllowedActions(network="deny"),
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="check",
                    argv=["python", "-c", "print('ok')"],
                    env={"A": "1", "B": "2"},
                )
            ]
        ),
    )


def _without_identity_and_mount(argv: list[str]) -> list[str]:
    result = list(argv)
    for flag in ("--cidfile", "--name", "--mount"):
        if flag in result:
            index = result.index(flag)
            del result[index : index + 2]
    return result


def test_named_volume_runner_preserves_direct_container_policy(tmp_path: Path) -> None:
    capsule = _capsule()
    check = capsule.success.commands[0]
    direct = build_container_argv(
        capsule,
        check,
        tmp_path.resolve(),
        "nested",
        tmp_path / "cid",
        runtime_binary="docker-test",
    )
    remote = build_container_volume_argv(
        capsule,
        check,
        "e2h-replay-workspace-abc",
        "nested",
        "e2h-replay-check-def",
        runtime_binary="docker-test",
    )

    assert _without_identity_and_mount(remote) == _without_identity_and_mount(direct)
