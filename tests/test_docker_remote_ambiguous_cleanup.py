from __future__ import annotations

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


def test_volume_create_failure_still_attempts_named_volume_cleanup(
    monkeypatch: pytest.MonkeyPatch,
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
            raise DockerRemoteError("lost response after daemon-side volume create")
        return ""

    monkeypatch.setattr(docker_remote, "_run_docker", fake_run_docker)

    with pytest.raises(DockerRemoteError, match="lost response"):
        with prepared_workspace_volume(
            ContainerSandbox(image=IMAGE),
            object(),  # type: ignore[arg-type]
        ):
            raise AssertionError("failed create must not yield")

    assert len(calls) == 2
    assert calls[0][:2] == ["volume", "create"]
    assert calls[1][:3] == ["volume", "rm", "-f"]
    assert calls[1][-1] == calls[0][-1]


def test_container_create_failure_still_attempts_named_container_cleanup(
    monkeypatch: pytest.MonkeyPatch,
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
            raise DockerRemoteError("lost response after daemon-side container create")
        return ""

    monkeypatch.setattr(docker_remote, "_run_docker", fake_run_docker)

    with pytest.raises(DockerRemoteError, match="lost response"):
        with prepared_workspace_volume(
            ContainerSandbox(image=IMAGE),
            object(),  # type: ignore[arg-type]
        ):
            raise AssertionError("failed create must not yield")

    assert [args[0] for args in calls] == ["volume", "create", "rm", "volume"]
    create_args = calls[1]
    container_name = create_args[create_args.index("--name") + 1]
    volume_name = calls[0][-1]
    assert calls[2] == ["rm", "-f", container_name]
    assert calls[3] == ["volume", "rm", "-f", volume_name]


def test_primary_failure_retains_cleanup_failure_as_exception_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bypass_precreate_dependencies(monkeypatch)

    def fake_run_docker(
        runtime: str,
        args: list[str],
        **kwargs: Any,
    ) -> str:
        del runtime, kwargs
        if args[:2] == ["volume", "create"]:
            return args[-1]
        if args and args[0] == "create":
            return "a" * 64
        if args[:2] == ["volume", "rm"]:
            raise DockerRemoteError("volume cleanup failed")
        return ""

    monkeypatch.setattr(docker_remote, "_run_docker", fake_run_docker)

    with pytest.raises(ValueError, match="body failed") as caught:
        with prepared_workspace_volume(
            ContainerSandbox(image=IMAGE),
            object(),  # type: ignore[arg-type]
        ):
            raise ValueError("body failed")

    assert str(caught.value) == "body failed"
    assert caught.value.__notes__ == [
        "Docker workspace cleanup failed: volume cleanup failed"
    ]


def test_run_docker_wraps_stdin_rewind_failure() -> None:
    class BrokenStdin:
        def seek(self, offset: int) -> int:
            del offset
            raise OSError("injected rewind failure")

    with pytest.raises(DockerRemoteError, match="injected rewind failure"):
        docker_remote._run_docker(
            "docker",
            ["cp"],
            stdin=BrokenStdin(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("value", ["29.7.2-rc.1", "30.0.0-beta1", "30.0.0-preview.2"])
def test_docker_prerelease_versions_do_not_satisfy_security_gate(value: str) -> None:
    with pytest.raises(DockerRemoteError, match="prerelease version is not accepted"):
        docker_remote._parse_version(value, noun="client")
