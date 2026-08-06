from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

import e2h.runner as runner_module
from e2h.failures import FailureCode, FailureImpact
from e2h.models import TaskCapsule
from e2h.runner import (
    CheckStatus,
    ExecutionBackend,
    RunStatus,
    _ProcessOutcome,
    run_capsule,
)
from e2h.sandbox import SandboxError


def capsule(commands: list[dict[str, object]], *, sandbox: bool = False) -> TaskCapsule:
    payload: dict[str, object] = {
        "id": "failure-runner",
        "goal": "Classify observable failures",
        "success": {"commands": commands},
    }
    if sandbox:
        payload["sandbox"] = {
            "image": "example.invalid/e2h@sha256:" + "a" * 64,
        }
    return TaskCapsule.model_validate(payload)


def test_nonzero_exit_and_skipped_check_receive_linked_taxonomy(tmp_path: Path) -> None:
    result = run_capsule(
        capsule(
            [
                {"id": "contract", "argv": [sys.executable, "-c", "raise SystemExit(7)"]},
                {"id": "blocked", "argv": [sys.executable, "-c", "print('no')"]},
            ]
        ),
        tmp_path,
    )
    assert result.status is RunStatus.FAILED
    failed, blocked = result.checks
    assert failed.status is CheckStatus.FAILED
    assert failed.failure is not None
    assert failed.failure.code is FailureCode.UNEXPECTED_EXIT
    assert failed.failure.details["actual_exit_code"] == 7
    assert blocked.failure is not None
    assert blocked.failure.code is FailureCode.SKIPPED_AFTER_FAILURE
    assert blocked.failure.caused_by_check_id == "contract"
    assert result.failure_summary.total == 2
    assert result.failure_summary.evaluation_failures == 1
    assert result.failure_summary.not_evaluated == 1
    assert result.failure_summary.primary_check_id == "contract"


def test_timeout_receives_resource_failure(tmp_path: Path) -> None:
    result = run_capsule(
        capsule(
            [
                {
                    "id": "slow",
                    "argv": [sys.executable, "-c", "import time; time.sleep(1)"],
                    "timeout_seconds": 0.05,
                }
            ]
        ),
        tmp_path,
    )
    failure = result.checks[0].failure
    assert failure is not None
    assert failure.code is FailureCode.TIMEOUT
    assert failure.details["timeout_seconds"] == 0.05
    assert failure.details["backend"] == "local"
    assert result.failure_summary.primary_code is FailureCode.TIMEOUT


@pytest.mark.skipif(os.name != "posix", reason="negative signal exit codes are POSIX-specific")
def test_signal_termination_is_distinct_from_unexpected_exit(tmp_path: Path) -> None:
    code = "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"
    result = run_capsule(
        capsule([{"id": "signal", "argv": [sys.executable, "-c", code]}]),
        tmp_path,
    )
    failure = result.checks[0].failure
    assert failure is not None
    assert failure.code is FailureCode.SIGNAL_TERMINATION
    assert failure.details["signal"] == signal.SIGTERM


def test_missing_command_is_dependency_failure(tmp_path: Path) -> None:
    result = run_capsule(
        capsule([{"id": "missing", "argv": ["e2h-command-that-does-not-exist"]}]),
        tmp_path,
    )
    failure = result.checks[0].failure
    assert result.status is RunStatus.ERROR
    assert failure is not None
    assert failure.code is FailureCode.COMMAND_NOT_FOUND
    assert failure.impact is FailureImpact.INFRASTRUCTURE_ERROR
    assert result.failure_summary.infrastructure_errors == 1


def test_permission_launch_error_is_typed_without_message_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(*args, **kwargs):
        raise PermissionError(13, "arbitrary provider text")

    monkeypatch.setattr(runner_module, "_execute_local_command", deny)
    result = run_capsule(
        capsule([{"id": "denied", "argv": [sys.executable, "-V"]}]),
        tmp_path,
    )
    failure = result.checks[0].failure
    assert failure is not None
    assert failure.code is FailureCode.PERMISSION_DENIED
    assert "arbitrary provider text" not in failure.model_dump_json()


def test_missing_check_directory_is_environment_failure(tmp_path: Path) -> None:
    result = run_capsule(
        capsule([{"id": "missing-cwd", "cwd": "missing", "argv": [sys.executable, "-V"]}]),
        tmp_path,
    )
    failure = result.checks[0].failure
    assert failure is not None
    assert failure.code is FailureCode.WORKING_DIRECTORY_MISSING
    assert result.failure_summary.primary_check_id == "missing-cwd"


def test_output_capture_failure_is_infrastructure_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_module,
        "_execute_local_command",
        lambda *args, **kwargs: _ProcessOutcome(
            exit_code=0,
            timed_out=False,
            stdout="partial",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            error="reader failed",
            failure_code=FailureCode.OUTPUT_CAPTURE,
        ),
    )
    result = run_capsule(
        capsule([{"id": "capture", "argv": [sys.executable, "-V"]}]),
        tmp_path,
    )
    failure = result.checks[0].failure
    assert result.status is RunStatus.ERROR
    assert failure is not None
    assert failure.code is FailureCode.OUTPUT_CAPTURE
    assert "reader failed" not in failure.model_dump_json()


def test_container_timeout_cleanup_failure_preserves_timeout_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_module,
        "_execute_container_command",
        lambda *args, **kwargs: _ProcessOutcome(
            exit_code=None,
            timed_out=True,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
            error="cleanup failed",
            failure_code=FailureCode.SANDBOX_CLEANUP,
        ),
    )
    result = run_capsule(
        capsule(
            [{"id": "container-timeout", "argv": ["python", "-V"], "timeout_seconds": 1}],
            sandbox=True,
        ),
        tmp_path,
        backend=ExecutionBackend.CONTAINER,
    )
    failure = result.checks[0].failure
    assert result.status is RunStatus.ERROR
    assert failure is not None
    assert failure.code is FailureCode.SANDBOX_CLEANUP
    assert failure.causes == [FailureCode.TIMEOUT]


def test_sandbox_exception_is_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):
        raise SandboxError("opaque runtime detail")

    monkeypatch.setattr(runner_module, "_execute_container_command", fail)
    result = run_capsule(
        capsule([{"id": "sandbox", "argv": ["python", "-V"]}], sandbox=True),
        tmp_path,
        backend=ExecutionBackend.CONTAINER,
    )
    failure = result.checks[0].failure
    assert failure is not None
    assert failure.code is FailureCode.SANDBOX_RUNTIME
    assert "opaque runtime detail" not in failure.model_dump_json()


def test_passed_run_has_empty_failure_summary(tmp_path: Path) -> None:
    result = run_capsule(
        capsule([{"id": "ok", "argv": [sys.executable, "-c", "print('ok')"]}]),
        tmp_path,
    )
    assert result.checks[0].failure is None
    assert result.failure_summary.total == 0
    assert result.failure_summary.by_code == {}
