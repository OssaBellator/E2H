from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def test_container_runner_disables_image_defined_healthchecks(tmp_path: Path) -> None:
    capsule = TaskCapsule(
        id="healthcheck-boundary",
        goal="Run only the declared replay check.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["true"])]),
    )

    argv = build_container_argv(
        capsule,
        capsule.success.commands[0],
        tmp_path.resolve(),
        ".",
        tmp_path / "cid",
    )

    assert argv.count("--no-healthcheck") == 1
    assert argv.index("--no-healthcheck") < argv.index(IMAGE)
