from __future__ import annotations

import io
import tarfile
from typing import Any

import pytest

import e2h.volume_runner as volume_runner
from e2h.failures import FailureCode
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, RunStatus, _ProcessOutcome
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
        id="launch-cleanup-precedence",
        goal="Make retained-container cleanup failure visible after launch errors.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )


def _created_state() -> volume_runner._DockerContainerState:
    return volume_runner._DockerContainerState(
        status="created",
        running=False,
        exit_code=0,
        error="",
        oom_killed=False,
    )


def _successful_create() -> _ProcessOutcome:
    return _ProcessOutcome(
        exit_code=0,
        timed_out=False,
        stdout=CONTAINER_ID + "\n",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
    )


def _assert_cleanup_priority(result: object, launch_text: str, cleanup_text: str) -> None:
    assert isinstance(result, volume_runner.RunResult)
    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_CLEANUP
    error = result.checks[0].error or ""
    assert launch_text in error
    assert cleanup_text in error


def test_create_launch_error_plus_cleanup_failure_is_sandbox_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_create(*args: Any, **kwargs: Any) -> _ProcessOutcome:
        del args, kwargs
        raise OSError("create launch failed")

    monkeypatch.setattr(volume_runner, "_execute_process", fail_create)
    monkeypatch.setattr(
        volume_runner,
        "force_remove_named_container",
        lambda runtime, name: "generated-name cleanup could not be proven",
    )

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    _assert_cleanup_priority(
        result,
        "create launch failed",
        "generated-name cleanup could not be proven",
    )


def test_start_launch_error_plus_confirmed_cleanup_failure_is_sandbox_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fail_start(argv: list[str], *args: Any, **kwargs: Any) -> _ProcessOutcome:
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            assert argv[1] == "create"
            return _successful_create()
        assert argv == ["docker-test", "start", "--attach", CONTAINER_ID]
        raise OSError("start launch failed")

    monkeypatch.setattr(volume_runner, "_execute_process", fail_start)
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda runtime, identity: _created_state()
        if identity == CONTAINER_ID
        else pytest.fail("pre-start inspect used the generated name"),
    )
    monkeypatch.setattr(
        volume_runner,
        "force_remove_confirmed_container",
        lambda runtime, container_id: "confirmed-ID cleanup could not be proven",
    )

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    _assert_cleanup_priority(
        result,
        "start launch failed",
        "confirmed-ID cleanup could not be proven",
    )
