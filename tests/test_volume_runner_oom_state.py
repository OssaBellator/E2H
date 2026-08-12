from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

import e2h.volume_runner as volume_runner
from e2h.failures import FailureCode
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import CheckStatus, RunStatus, _ProcessOutcome
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
        id="oom-state",
        goal="Do not treat Docker OOM termination as a successful expected exit.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(
            commands=[
                CommandCheck(
                    id="check",
                    argv=["check"],
                    expected_exit_codes={137},
                )
            ]
        ),
    )


def _outcome(
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
) -> _ProcessOutcome:
    return _ProcessOutcome(
        exit_code=exit_code,
        timed_out=False,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=False,
        stderr_truncated=False,
    )


def _install_state_runtime(
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
) -> list[str]:
    cleanup_names: list[str] = []

    def fake_execute(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout: float,
        max_output_chars: int,
    ) -> _ProcessOutcome:
        del cwd, env, timeout, max_output_chars
        if argv[1] == "run":
            return _outcome(exit_code=137, stdout="task output\n")
        if argv[1] == "inspect":
            return _outcome(exit_code=0, stdout=json.dumps(state))
        raise AssertionError(f"unexpected runtime command: {argv}")

    monkeypatch.setattr(volume_runner, "_execute_process", fake_execute)
    monkeypatch.setattr(
        volume_runner,
        "force_remove_named_container",
        lambda runtime, name: cleanup_names.append(name) or None,
    )
    return cleanup_names


def _state(*, oom_killed: object) -> dict[str, Any]:
    return {
        "Status": "exited",
        "Running": False,
        "ExitCode": 137,
        "Error": "",
        "OOMKilled": oom_killed,
    }


def test_oom_killed_expected_137_is_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_names = _install_state_runtime(monkeypatch, _state(oom_killed=True))

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].exit_code == 137
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_RUNTIME
    assert "OOM-killed" in (result.checks[0].error or "")
    assert len(cleanup_names) == 1
    assert cleanup_names[0].startswith("e2h-replay-check-")


def test_non_oom_expected_137_remains_normal_task_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_names = _install_state_runtime(monkeypatch, _state(oom_killed=False))

    result = run_capsule_prepared_volume(
        _capsule(),
        _archive(),
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.PASSED
    assert result.checks[0].status is CheckStatus.PASSED
    assert result.checks[0].exit_code == 137
    assert result.checks[0].failure is None
    assert len(cleanup_names) == 1


def test_invalid_oom_killed_state_fails_closed_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_names = _install_state_runtime(monkeypatch, _state(oom_killed="yes"))

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
    assert "invalid fields" in (result.checks[0].error or "")
    assert len(cleanup_names) == 1
