from __future__ import annotations

import io
import tarfile
from typing import Any

import pytest

import e2h.volume_runner as volume_runner
from e2h.failures import FailureCode
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, RunStatus, _ProcessOutcome
from e2h.workspace_archive import WorkspaceArchive

IMAGE = "python@sha256:" + "0" * 64


def _archive(*, directories: tuple[str, ...] = (".",)) -> WorkspaceArchive:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        for name in directories:
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            handle.addfile(member)
    archive_bytes = stream.tell()
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset(directories),
        source_bytes=0,
        entries=len(directories) - 1,
        archive_bytes=archive_bytes,
    )


def _capsule(commands: list[CommandCheck]) -> TaskCapsule:
    return TaskCapsule(
        id="remote-infrastructure-halt",
        goal="Never launch later checks after remote infrastructure becomes unreliable.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=commands),
    )


def test_remote_cleanup_failure_halts_even_when_check_requests_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_execute(
        capsule: TaskCapsule,
        check: CommandCheck,
        volume_name: str,
        relative_cwd: str,
        timeout: float,
        max_output_chars: int,
        runtime_binary: str | None,
    ) -> _ProcessOutcome:
        del capsule, volume_name, relative_cwd, timeout, max_output_chars, runtime_binary
        calls.append(check.id)
        return _ProcessOutcome(
            exit_code=None,
            timed_out=False,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            error="cleanup failed",
            failure_code=FailureCode.SANDBOX_CLEANUP,
        )

    monkeypatch.setattr(volume_runner, "_execute_volume_command", fake_execute)
    capsule = _capsule(
        [
            CommandCheck(id="first", argv=["first"], continue_on_failure=True),
            CommandCheck(id="second", argv=["second"]),
        ]
    )

    result = volume_runner.run_capsule_prepared_volume(
        capsule,
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.ERROR
    assert [check.status for check in result.checks] == [CheckStatus.ERROR, CheckStatus.SKIPPED]
    assert calls == ["first"]
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_CLEANUP
    assert result.checks[1].failure is not None
    assert result.checks[1].failure.caused_by_check_id == "first"


def test_missing_remote_check_directory_halts_even_when_check_requests_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_execute(*args: Any, **kwargs: Any) -> _ProcessOutcome:
        del args, kwargs
        raise AssertionError("later remote checks must not launch after infrastructure failure")

    monkeypatch.setattr(volume_runner, "_execute_volume_command", forbidden_execute)
    capsule = _capsule(
        [
            CommandCheck(
                id="missing",
                argv=["never"],
                cwd="missing",
                continue_on_failure=True,
            ),
            CommandCheck(id="second", argv=["second"], cwd="valid"),
        ]
    )

    result = volume_runner.run_capsule_prepared_volume(
        capsule,
        _archive(directories=(".", "valid")),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.ERROR
    assert [check.status for check in result.checks] == [CheckStatus.ERROR, CheckStatus.SKIPPED]
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.WORKING_DIRECTORY_MISSING
    assert result.checks[1].failure is not None
    assert result.checks[1].failure.caused_by_check_id == "missing"
