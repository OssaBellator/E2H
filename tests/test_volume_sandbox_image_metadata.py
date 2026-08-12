from __future__ import annotations

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_volume_argv

IMAGE = "python@sha256:" + "0" * 64


def test_volume_runner_ignores_image_execution_hooks() -> None:
    capsule = TaskCapsule(
        id="remote-image-execution-metadata-boundary",
        goal="Run only the declared replay check.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(
            commands=[CommandCheck(id="check", argv=["python", "-c", "print('ok')"])]
        ),
    )

    argv = build_container_volume_argv(
        capsule,
        capsule.success.commands[0],
        "e2h-replay-workspace-abc",
        ".",
        "e2h-replay-check-def",
        runtime_binary="docker-test",
    )

    assert argv.count("--no-healthcheck") == 1
    entrypoint = argv.index("--entrypoint")
    assert argv[entrypoint + 1] == ""
    image = argv.index(IMAGE)
    assert entrypoint < image
    assert argv[image + 1 :] == ["python", "-c", "print('ok')"]
