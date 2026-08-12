from __future__ import annotations

from pathlib import Path

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import build_container_argv

IMAGE = "python@sha256:" + "0" * 64


def _argv(tmp_path: Path, memory_mb: int) -> list[str]:
    capsule = TaskCapsule(
        id="bounded-memory-swap",
        goal="Make combined memory and swap match the capsule memory limit.",
        sandbox=ContainerSandbox(image=IMAGE, memory_mb=memory_mb),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )
    return build_container_argv(
        capsule,
        capsule.success.commands[0],
        tmp_path.resolve(),
        ".",
        tmp_path / "cid",
        runtime_binary="docker-test",
    )


@staticmethod
def _value(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_container_builder_disables_swap_beyond_memory_limit(tmp_path: Path) -> None:
    argv = _argv(tmp_path, 256)

    assert argv.count("--memory") == 1
    assert argv.count("--memory-swap") == 1
    assert _value(argv, "--memory") == "256m"
    assert _value(argv, "--memory-swap") == "256m"


def test_custom_memory_limit_updates_memory_and_swap_together(tmp_path: Path) -> None:
    argv = _argv(tmp_path, 1024)

    assert _value(argv, "--memory") == "1024m"
    assert _value(argv, "--memory-swap") == "1024m"
