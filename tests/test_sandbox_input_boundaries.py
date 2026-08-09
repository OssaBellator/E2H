from __future__ import annotations

from pathlib import Path

import pytest

from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.sandbox import SandboxError, build_container_argv, force_remove_container

IMAGE = "python@sha256:" + "0" * 64


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="sandbox-boundary",
        goal="Build a sandbox command.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["python", "-V"])]),
    )


def test_builder_revalidates_mutated_capsule(tmp_path: Path) -> None:
    capsule = _capsule()
    assert capsule.sandbox is not None
    capsule.sandbox.image = "bad\x00image@sha256:" + "0" * 64

    with pytest.raises(SandboxError, match="invalid task capsule"):
        build_container_argv(
            capsule,
            capsule.success.commands[0],
            tmp_path,
            ".",
            tmp_path / "cid",
        )


def test_builder_revalidates_mutated_command_check(tmp_path: Path) -> None:
    capsule = _capsule()
    check = capsule.success.commands[0].model_copy(deep=True)
    check.env["BAD=KEY"] = "value"

    with pytest.raises(SandboxError, match="invalid command check"):
        build_container_argv(capsule, check, tmp_path, ".", tmp_path / "cid")


@pytest.mark.parametrize("runtime", ["", "docker\x00bad"])
def test_builder_rejects_unsafe_runtime_binary(tmp_path: Path, runtime: str) -> None:
    capsule = _capsule()

    with pytest.raises(SandboxError, match="runtime binary"):
        build_container_argv(
            capsule,
            capsule.success.commands[0],
            tmp_path,
            ".",
            tmp_path / "cid",
            runtime_binary=runtime,
        )


@pytest.mark.parametrize("argument", ["cwd", "workspace", "cidfile"])
def test_builder_rejects_nul_filesystem_arguments(tmp_path: Path, argument: str) -> None:
    capsule = _capsule()
    workspace = tmp_path
    relative_cwd = "."
    cidfile = tmp_path / "cid"
    if argument == "cwd":
        relative_cwd = "bad\x00cwd"
    elif argument == "workspace":
        workspace = Path("bad\x00workspace")
    else:
        cidfile = Path("bad\x00cid")

    with pytest.raises(SandboxError, match="must not contain NUL"):
        build_container_argv(
            capsule,
            capsule.success.commands[0],
            workspace,
            relative_cwd,
            cidfile,
        )


def test_cleanup_rejects_unsafe_runtime_binary(tmp_path: Path) -> None:
    cidfile = tmp_path / "cid"
    cidfile.write_text("a" * 64, encoding="utf-8")

    assert force_remove_container("docker\x00bad", cidfile) == (
        "container runtime binary must be non-empty and contain no NUL"
    )
