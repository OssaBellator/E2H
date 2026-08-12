from __future__ import annotations

import io
import tarfile
from typing import Any

import pytest

import e2h.volume_runner as volume_runner
from e2h.failures import FailureCode
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, RunStatus, _ProcessOutcome
from e2h.sandbox import SandboxError
from e2h.volume_runner import run_capsule_prepared_volume
from e2h.workspace_archive import WorkspaceArchive

IMAGE = "python@sha256:" + "0" * 64
CONTAINER_ID = "a" * 64


def _archive() -> WorkspaceArchive:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
    archive_bytes = stream.tell()
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset({"."}),
        source_bytes=0,
        entries=0,
        archive_bytes=archive_bytes,
    )


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="retained-control-failures",
        goal="Exercise retained-container control-plane failure precedence.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )


def _outcome(
    *,
    exit_code: int = 0,
    timed_out: bool = False,
    error: str | None = None,
    stdout: str = CONTAINER_ID + "\n",
) -> _ProcessOutcome:
    return _ProcessOutcome(
        exit_code=exit_code,
        timed_out=timed_out,
        stdout=stdout,
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        error=error,
        failure_code=FailureCode.OUTPUT_CAPTURE if error is not None else None,
    )


def _created_state() -> volume_runner._DockerContainerState:
    return volume_runner._DockerContainerState(
        status="created",
        running=False,
        exit_code=0,
        error="",
    )


def _exited_state() -> volume_runner._DockerContainerState:
    return volume_runner._DockerContainerState(
        status="exited",
        running=False,
        exit_code=0,
        error="",
    )


def test_create_timeout_never_starts_check(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_execute(argv: list[str], *args: Any, **kwargs: Any) -> _ProcessOutcome:
        commands.append(argv)
        assert argv[1] == "create"
        return _outcome(exit_code=None, timed_out=True, stdout="")

    monkeypatch.setattr(volume_runner, "_execute_process", fake_execute)
    monkeypatch.setattr(volume_runner, "force_remove_named_container", lambda *args: None)

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert [argv[1] for argv in commands] == ["create"]
    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_RUNTIME
    assert "creation timed out" in (result.checks[0].error or "")


def test_invalid_create_id_never_starts_and_uses_name_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    cleanup_names: list[str] = []

    def fake_execute(argv: list[str], *args: Any, **kwargs: Any) -> _ProcessOutcome:
        del args, kwargs
        commands.append(argv)
        assert argv[1] == "create"
        return _outcome(stdout="not-a-container-id\n")

    monkeypatch.setattr(volume_runner, "_execute_process", fake_execute)
    monkeypatch.setattr(
        volume_runner,
        "force_remove_named_container",
        lambda runtime, name: cleanup_names.append(name) or None,
    )

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert [argv[1] for argv in commands] == ["create"]
    assert len(cleanup_names) == 1
    assert cleanup_names[0].startswith("e2h-replay-check-")
    assert result.status is RunStatus.ERROR
    assert "invalid full container ID" in (result.checks[0].error or "")


def test_noncreated_prestart_state_never_starts_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    cleanup_ids: list[str] = []

    def fake_execute(argv: list[str], *args: Any, **kwargs: Any) -> _ProcessOutcome:
        commands.append(argv)
        assert argv[1] == "create"
        return _outcome()

    monkeypatch.setattr(volume_runner, "_execute_process", fake_execute)
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda *args: volume_runner._DockerContainerState(
            status="running",
            running=True,
            exit_code=0,
            error="",
        ),
    )
    monkeypatch.setattr(
        volume_runner,
        "force_remove_confirmed_container",
        lambda runtime, container_id: cleanup_ids.append(container_id) or None,
    )

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert [argv[1] for argv in commands] == ["create"]
    assert cleanup_ids == [CONTAINER_ID]
    assert result.status is RunStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_RUNTIME
    assert "stopped created state" in (result.checks[0].error or "")


def test_inspect_failure_with_successful_cleanup_is_sandbox_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(volume_runner, "_execute_process", lambda *args, **kwargs: _outcome())
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(SandboxError("inspect failed")),
    )
    monkeypatch.setattr(volume_runner, "force_remove_confirmed_container", lambda *args: None)

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_RUNTIME
    assert "inspect failed" in (result.checks[0].error or "")


def test_inspect_and_cleanup_failure_prioritizes_sandbox_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(volume_runner, "_execute_process", lambda *args, **kwargs: _outcome())
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(SandboxError("inspect failed")),
    )
    monkeypatch.setattr(
        volume_runner,
        "force_remove_confirmed_container",
        lambda *args: "cleanup failed",
    )

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_CLEANUP
    assert "inspect failed" in (result.checks[0].error or "")
    assert "cleanup failed" in (result.checks[0].error or "")


def test_output_capture_failure_uses_confirmed_id_for_control_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter([_created_state(), _exited_state()])
    inspected: list[str] = []
    cleaned: list[str] = []

    def fake_execute(argv: list[str], *args: Any, **kwargs: Any) -> _ProcessOutcome:
        if argv[1] == "create":
            return _outcome()
        assert argv == ["docker-test", "start", "--attach", CONTAINER_ID]
        return _outcome(error="capture failed", stdout="task output\n")

    def inspect(runtime: str, identity: str) -> volume_runner._DockerContainerState:
        assert runtime == "docker-test"
        inspected.append(identity)
        return next(states)

    monkeypatch.setattr(volume_runner, "_execute_process", fake_execute)
    monkeypatch.setattr(volume_runner, "_inspect_named_container_state", inspect)
    monkeypatch.setattr(
        volume_runner,
        "force_remove_confirmed_container",
        lambda runtime, container_id: cleaned.append(container_id) or None,
    )

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert inspected == [CONTAINER_ID, CONTAINER_ID]
    assert cleaned == [CONTAINER_ID]
    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.OUTPUT_CAPTURE
    assert result.checks[0].error == "capture failed"
