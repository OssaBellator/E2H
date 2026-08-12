from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import e2h.docker_remote as docker_remote
from e2h.docker_remote import DockerRemoteError, prepared_workspace_volume
from e2h.models import ContainerSandbox

IMAGE = "python@sha256:" + "0" * 64


def _bypass_precreate_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        docker_remote,
        "_validated_workspace_archive",
        lambda archive: archive,
    )
    monkeypatch.setattr(
        docker_remote,
        "require_patched_docker_archive",
        lambda runtime_binary: None,
    )
    monkeypatch.setattr(
        docker_remote,
        "_require_volume_free_image",
        lambda runtime, image: None,
    )


@pytest.mark.parametrize(
    "created_container",
    ["short-id", "a" * 63, "A" * 64, "g" * 64],
)
def test_invalid_preparation_container_id_fails_before_archive_copy_and_cleans_name(
    monkeypatch: pytest.MonkeyPatch,
    created_container: str,
) -> None:
    _bypass_precreate_dependencies(monkeypatch)
    calls: list[list[str]] = []

    def fake_run_docker(
        runtime: str,
        args: list[str],
        **kwargs: Any,
    ) -> str:
        del runtime, kwargs
        calls.append(list(args))
        if args[:2] == ["volume", "create"]:
            return args[-1]
        if args and args[0] == "create":
            return created_container
        if args and args[0] == "cp":
            raise AssertionError("archive copy reached Docker after malformed create identity")
        return ""

    monkeypatch.setattr(docker_remote, "_run_docker", fake_run_docker)

    with pytest.raises(
        DockerRemoteError,
        match="invalid preparation container ID",
    ), prepared_workspace_volume(
        ContainerSandbox(image=IMAGE),
        object(),  # type: ignore[arg-type]
    ):
        raise AssertionError("malformed preparation identity must not yield")

    assert [args[0] for args in calls] == ["volume", "create", "rm", "volume"]
    create = calls[1]
    container_name = create[create.index("--name") + 1]
    volume_name = calls[0][-1]
    assert calls[2] == ["rm", "-f", "-v", container_name]
    assert calls[3] == ["volume", "rm", "-f", volume_name]


def test_confirmed_preparation_container_id_is_used_for_copy_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bypass_precreate_dependencies(monkeypatch)
    calls: list[list[str]] = []
    container_id = "b" * 64

    def fake_run_docker(
        runtime: str,
        args: list[str],
        **kwargs: Any,
    ) -> str:
        del runtime, kwargs
        calls.append(list(args))
        if args[:2] == ["volume", "create"]:
            return args[-1]
        if args and args[0] == "create":
            return container_id
        return ""

    monkeypatch.setattr(docker_remote, "_run_docker", fake_run_docker)
    archive = SimpleNamespace(file=object())

    with prepared_workspace_volume(
        ContainerSandbox(image=IMAGE),
        archive,  # type: ignore[arg-type]
    ) as volume_name:
        assert volume_name.startswith("e2h-replay-workspace-")

    assert [args[0] for args in calls] == ["volume", "create", "cp", "rm", "volume"]
    create = calls[1]
    generated_name = create[create.index("--name") + 1]
    assert generated_name != container_id
    assert calls[2] == ["cp", "--quiet", "-", f"{container_id}:/workspace"]
    assert calls[3] == ["rm", "-f", "-v", container_id]
    assert calls[4] == ["volume", "rm", "-f", volume_name]


def test_archive_copy_failure_cleans_confirmed_preparation_container_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bypass_precreate_dependencies(monkeypatch)
    calls: list[list[str]] = []
    container_id = "c" * 64

    def fake_run_docker(
        runtime: str,
        args: list[str],
        **kwargs: Any,
    ) -> str:
        del runtime, kwargs
        calls.append(list(args))
        if args[:2] == ["volume", "create"]:
            return args[-1]
        if args and args[0] == "create":
            return container_id
        if args and args[0] == "cp":
            raise DockerRemoteError("archive copy failed")
        return ""

    monkeypatch.setattr(docker_remote, "_run_docker", fake_run_docker)
    archive = SimpleNamespace(file=object())

    with pytest.raises(DockerRemoteError, match="archive copy failed"):
        with prepared_workspace_volume(
            ContainerSandbox(image=IMAGE),
            archive,  # type: ignore[arg-type]
        ):
            raise AssertionError("failed archive copy must not yield")

    assert [args[0] for args in calls] == ["volume", "create", "cp", "rm", "volume"]
    assert calls[3] == ["rm", "-f", "-v", container_id]
