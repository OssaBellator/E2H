from __future__ import annotations

import io
import tarfile

import pytest

import e2h.volume_runner as volume_runner
from e2h.failures import FailureCode
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, RunStatus, _ProcessOutcome
from e2h.sandbox import SandboxError
from e2h.volume_runner import run_capsule_prepared_volume
from e2h.workspace_archive import WorkspaceArchive

IMAGE = "python@sha256:" + "0" * 64


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


def _run_outcome(*, error: str | None = None) -> _ProcessOutcome:
    return _ProcessOutcome(
        exit_code=0,
        timed_out=False,
        stdout="task output\n",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        error=error,
        failure_code=FailureCode.OUTPUT_CAPTURE if error is not None else None,
    )


def _exited_state() -> volume_runner._DockerContainerState:
    return volume_runner._DockerContainerState(
        status="exited",
        running=False,
        exit_code=0,
        error="",
    )


def test_inspect_failure_with_successful_cleanup_is_sandbox_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(volume_runner, "_execute_process", lambda *args, **kwargs: _run_outcome())
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(SandboxError("inspect failed")),
    )
    monkeypatch.setattr(volume_runner, "force_remove_named_container", lambda *args: None)

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
    monkeypatch.setattr(volume_runner, "_execute_process", lambda *args, **kwargs: _run_outcome())
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(SandboxError("inspect failed")),
    )
    monkeypatch.setattr(
        volume_runner,
        "force_remove_named_container",
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


def test_output_capture_failure_survives_healthy_inspect_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        volume_runner,
        "_execute_process",
        lambda *args, **kwargs: _run_outcome(error="capture failed"),
    )
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda *args, **kwargs: _exited_state(),
    )
    monkeypatch.setattr(volume_runner, "force_remove_named_container", lambda *args: None)

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.OUTPUT_CAPTURE
    assert result.checks[0].error == "capture failed"
