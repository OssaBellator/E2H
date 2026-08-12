from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def _argv(tmp_path: Path) -> list[str]:
    capsule = TaskCapsule(
        id="image-execution-metadata-boundary",
        goal="Run only the declared replay check.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["true"])]),
    )
    return build_container_argv(
        capsule,
        capsule.success.commands[0],
        tmp_path.resolve(),
        ".",
        tmp_path / "cid",
    )


def test_container_runner_disables_image_defined_healthchecks(tmp_path: Path) -> None:
    argv = _argv(tmp_path)

    assert argv.count("--no-healthcheck") == 1
    assert argv.index("--no-healthcheck") < argv.index(IMAGE)


def test_container_runner_clears_image_entrypoint(tmp_path: Path) -> None:
    argv = _argv(tmp_path)

    index = argv.index("--entrypoint")
    assert argv[index + 1] == ""
    assert index < argv.index(IMAGE)
    assert argv[argv.index(IMAGE) + 1 :] == ["true"]
