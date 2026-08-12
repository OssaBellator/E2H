from __future__ import annotations

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_volume_argv, build_container_volume_create_argv

IMAGE = "python@sha256:" + "0" * 64


def test_stopped_container_builder_preserves_remote_policy() -> None:
    capsule = TaskCapsule(
        id="remote-create",
        goal="Create the replay container without starting the declared check.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["python", "-V"])]),
    )
    check = capsule.success.commands[0]
    run_argv = build_container_volume_argv(
        capsule,
        check,
        "e2h-replay-workspace-abc",
        ".",
        "e2h-replay-check-def",
        runtime_binary="docker-test",
    )
    create_argv = build_container_volume_create_argv(
        capsule,
        check,
        "e2h-replay-workspace-abc",
        ".",
        "e2h-replay-check-def",
        runtime_binary="docker-test",
    )

    assert run_argv[1] == "run"
    assert create_argv[1] == "create"
    normalized = list(create_argv)
    normalized[1] = "run"
    assert normalized == run_argv
    assert "--rm" not in create_argv
    assert "--cidfile" not in create_argv
    assert create_argv[create_argv.index("--name") + 1] == "e2h-replay-check-def"
