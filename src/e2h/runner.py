"""Deterministic local replay runner for E2H task capsules."""

from __future__ import annotations

import os
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, Field, model_validator

from e2h.failures import (
    FailureCode,
    FailureImpact,
    FailureRecord,
    FailureSummary,
    launch_failure,
    output_capture_failure,
    sandbox_failure,
    skipped_failure,
    summarize_failures,
    timeout_failure,
    unexpected_exit_failure,
    working_directory_failure,
)
from e2h.models import CommandCheck, TaskCapsule
from e2h.sandbox import SandboxError, build_container_argv, force_remove_container

_OUTPUT_MARKER = "\n... <output truncated> ...\n"
_READ_CHUNK_BYTES = 64 * 1024
_TERMINATION_GRACE_SECONDS = 0.1
_READER_JOIN_SECONDS = 1.0


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ERROR = "error"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class ExecutionBackend(StrEnum):
    """Execution backend selected for capsule checks."""

    AUTO = "auto"
    LOCAL = "local"
    CONTAINER = "container"


class CommandResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    id: str
    argv: list[str]
    cwd: str
    status: CheckStatus
    exit_code: int | None = None
    duration_seconds: float = Field(ge=0)
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: str | None = None
    failure: FailureRecord | None = None

    @model_validator(mode="after")
    def failure_must_match_status(self) -> CommandResult:
        if self.status is CheckStatus.PASSED:
            if self.failure is not None:
                raise ValueError("passed command results must not define a failure")
        elif self.failure is None:
            raise ValueError("non-passed command results require a failure")
        return self


class RunResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        revalidate_instances="always",
    )

    capsule_id: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(ge=0)
    checks: list[CommandResult]
    failure_summary: FailureSummary = Field(default_factory=FailureSummary)

    @model_validator(mode="after")
    def result_must_be_consistent(self) -> RunResult:
        if self.started_at.tzinfo is None or self.started_at.utcoffset() is None:
            raise ValueError("run started_at must be timezone-aware")
        if self.finished_at.tzinfo is None or self.finished_at.utcoffset() is None:
            raise ValueError("run finished_at must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("run finished_at must not precede started_at")
        check_ids = [check.id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("run check ids must be unique")
        expected_summary = summarize_failures((check.id, check.failure) for check in self.checks)
        if self.failure_summary != expected_summary:
            raise ValueError("run failure_summary must match check failures")
        has_infrastructure_error = any(
            check.failure is not None
            and check.failure.impact is FailureImpact.INFRASTRUCTURE_ERROR
            for check in self.checks
        )
        has_failed_check = any(check.status is not CheckStatus.PASSED for check in self.checks)
        expected_status = (
            RunStatus.ERROR
            if has_infrastructure_error
            else RunStatus.FAILED
            if has_failed_check
            else RunStatus.PASSED
        )
        if self.status is not expected_status:
            raise ValueError("run status must match check outcomes")
        return self


class RunnerError(RuntimeError):
    """Raised when a replay cannot be safely started."""


def _resolve_workspace(workspace: Path) -> Path:
    if "\x00" in os.fspath(workspace):
        raise RunnerError("workspace path must not contain NUL")
    try:
        return workspace.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RunnerError(f"unable to resolve workspace: {exc}") from exc


class _BoundedCapture:
    """Drain a byte stream while retaining only enough data for a bounded text report."""

    def __init__(self, char_limit: int) -> None:
        self.char_limit = char_limit
        self.byte_limit = char_limit * 4
        self._head_limit = self.byte_limit // 2
        self._tail_limit = self.byte_limit - self._head_limit
        self._head = bytearray()
        self._tail = bytearray()
        self.total_bytes = 0
        self.error: str | None = None

    @property
    def retained_bytes(self) -> int:
        return len(self._head) + len(self._tail)

    def feed(self, chunk: bytes) -> None:
        self.total_bytes += len(chunk)
        if len(self._head) < self._head_limit:
            head_remaining = self._head_limit - len(self._head)
            self._head.extend(chunk[:head_remaining])
            chunk = chunk[head_remaining:]
        if not chunk or self._tail_limit == 0:
            return
        self._tail.extend(chunk)
        excess = len(self._tail) - self._tail_limit
        if excess > 0:
            del self._tail[:excess]

    def render(self) -> tuple[str, bool]:
        if self.total_bytes <= self.byte_limit:
            value = bytes(self._head + self._tail).decode("utf-8", errors="replace")
            return _truncate(value, self.char_limit)
        head = bytes(self._head).decode("utf-8", errors="replace")
        tail = bytes(self._tail).decode("utf-8", errors="replace")
        return _join_truncated(head, tail, self.char_limit)


@dataclass(frozen=True)
class _ProcessOutcome:
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    error: str | None = None
    failure_code: FailureCode | None = None


def _safe_child(root: Path, relative: str) -> Path:
    try:
        candidate = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise RunnerError(f"unable to resolve workspace path {relative!r}: {exc}") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RunnerError(f"path escapes workspace: {relative}") from exc
    return candidate


def _join_truncated(head: str, tail: str, limit: int) -> tuple[str, bool]:
    remaining = max(0, limit - len(_OUTPUT_MARKER))
    head_chars = remaining // 2
    tail_chars = remaining - head_chars
    suffix = tail[-tail_chars:] if tail_chars else ""
    return f"{head[:head_chars]}{_OUTPUT_MARKER}{suffix}", True


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return _join_truncated(value, value, limit)


def _drain_stream(stream: BinaryIO, capture: _BoundedCapture) -> None:
    try:
        while chunk := stream.read(_READ_CHUNK_BYTES):
            capture.feed(chunk)
    except (OSError, ValueError) as exc:
        capture.error = str(exc)
    finally:
        with suppress(OSError):
            stream.close()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        sleep(_TERMINATION_GRACE_SECONDS)
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    else:
        with suppress(OSError):
            process.terminate()
        try:
            process.wait(timeout=_TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                process.kill()
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_READER_JOIN_SECONDS)


def _execute_process(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    max_output_chars: int,
) -> _ProcessOutcome:
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    assert process.stdout is not None
    assert process.stderr is not None

    stdout_capture = _BoundedCapture(max_output_chars)
    stderr_capture = _BoundedCapture(max_output_chars)
    readers = [
        Thread(target=_drain_stream, args=(process.stdout, stdout_capture), daemon=True),
        Thread(target=_drain_stream, args=(process.stderr, stderr_capture), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = monotonic() + timeout
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True

    if not timed_out:
        for reader in readers:
            reader.join(timeout=max(0.0, deadline - monotonic()))
        timed_out = any(reader.is_alive() for reader in readers)

    if timed_out:
        _terminate_process_tree(process)

    for reader in readers:
        reader.join(timeout=_READER_JOIN_SECONDS)

    lingering_readers = any(reader.is_alive() for reader in readers)
    if lingering_readers:
        with suppress(OSError):
            process.stdout.close()
        with suppress(OSError):
            process.stderr.close()
        for reader in readers:
            reader.join(timeout=_READER_JOIN_SECONDS)

    stdout, stdout_truncated = stdout_capture.render()
    stderr, stderr_truncated = stderr_capture.render()
    capture_errors = [error for error in (stdout_capture.error, stderr_capture.error) if error]
    if any(reader.is_alive() for reader in readers):
        capture_errors.append("output reader did not terminate")
    capture_error = "; ".join(capture_errors) or None

    return _ProcessOutcome(
        exit_code=None if timed_out else process.returncode,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        error=capture_error,
        failure_code=FailureCode.OUTPUT_CAPTURE if capture_error is not None else None,
    )


def _execute_local_command(
    check: CommandCheck,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
) -> _ProcessOutcome:
    return _execute_process(
        check.argv,
        cwd,
        {**os.environ, **check.env},
        timeout,
        max_output_chars,
    )


def _execute_container_command(
    capsule: TaskCapsule,
    check: CommandCheck,
    workspace_root: Path,
    relative_cwd: str,
    timeout: float,
    max_output_chars: int,
    runtime_binary: str | None,
) -> _ProcessOutcome:
    if capsule.sandbox is None:
        raise SandboxError("container execution requires capsule.sandbox")
    runtime = runtime_binary or capsule.sandbox.engine
    with TemporaryDirectory(prefix="e2h-container-") as temporary:
        cidfile = Path(temporary) / "container.cid"
        argv = build_container_argv(
            capsule,
            check,
            workspace_root,
            relative_cwd,
            cidfile,
            runtime_binary=runtime,
        )
        outcome = _execute_process(
            argv,
            workspace_root,
            os.environ.copy(),
            timeout,
            max_output_chars,
        )
        if outcome.timed_out:
            cleanup_error = force_remove_container(runtime, cidfile)
            if cleanup_error is not None:
                combined = "; ".join(item for item in (outcome.error, cleanup_error) if item)
                outcome = replace(
                    outcome,
                    error=combined,
                    failure_code=FailureCode.SANDBOX_CLEANUP,
                )
        return outcome


def _skipped(check: CommandCheck, cwd: str, blocked_by_check_id: str) -> CommandResult:
    return CommandResult(
        id=check.id,
        argv=check.argv,
        cwd=cwd,
        status=CheckStatus.SKIPPED,
        duration_seconds=0,
        error="skipped after an earlier check failed",
        failure=skipped_failure(blocked_by_check_id),
    )


def _validated_capsule(capsule: TaskCapsule) -> TaskCapsule:
    """Return a detached, fully revalidated capsule before executable state is consumed."""
    if type(capsule) is not TaskCapsule:
        raise RunnerError(
            f"invalid task capsule: expected TaskCapsule, got {type(capsule).__name__}"
        )
    try:
        payload = capsule.model_dump(mode="python", warnings="none")
        return TaskCapsule.model_validate(payload)
    except ValueError as exc:
        raise RunnerError(f"invalid task capsule: {exc}") from exc


def run_capsule(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    backend: ExecutionBackend = ExecutionBackend.AUTO,
    container_runtime: str | None = None,
) -> RunResult:
    """Execute all command checks in a capsule inside a bounded workspace."""
    capsule = _validated_capsule(capsule)
    started_at = datetime.now(UTC)
    started_clock = monotonic()
    workspace_root = _resolve_workspace(workspace)
    if not workspace_root.is_dir():
        raise RunnerError(f"workspace is not a directory: {workspace}")

    task_root = _safe_child(workspace_root, capsule.initial_state.working_directory)
    if not task_root.is_dir():
        raise RunnerError(f"working directory does not exist: {task_root}")
    try:
        selected_backend = ExecutionBackend(backend)
    except ValueError as exc:
        raise RunnerError(f"unknown execution backend: {backend}") from exc
    if selected_backend is ExecutionBackend.AUTO:
        selected_backend = (
            ExecutionBackend.CONTAINER if capsule.sandbox is not None else ExecutionBackend.LOCAL
        )
    if selected_backend is ExecutionBackend.CONTAINER and capsule.sandbox is None:
        raise RunnerError("container backend requires capsule.sandbox")

    results: list[CommandResult] = []
    halt = False
    blocked_by_check_id: str | None = None
    infrastructure_error = False

    for check in capsule.success.commands:
        check_dir = _safe_child(task_root, check.cwd)
        relative_cwd = str(check_dir.relative_to(workspace_root)) or "."
        if halt:
            if blocked_by_check_id is None:
                raise RunnerError("halted replay is missing its blocking check")
            results.append(_skipped(check, relative_cwd, blocked_by_check_id))
            continue
        if not check_dir.is_dir():
            results.append(
                CommandResult(
                    id=check.id,
                    argv=check.argv,
                    cwd=relative_cwd,
                    status=CheckStatus.ERROR,
                    duration_seconds=0,
                    error=f"check directory does not exist: {check_dir}",
                    failure=working_directory_failure(),
                )
            )
            infrastructure_error = True
            blocked_by_check_id = check.id
            halt = not check.continue_on_failure
            continue

        timeout = check.timeout_seconds or capsule.limits.default_timeout_seconds
        command_started = monotonic()
        failure: FailureRecord | None = None
        try:
            if selected_backend is ExecutionBackend.CONTAINER:
                outcome = _execute_container_command(
                    capsule,
                    check,
                    workspace_root,
                    relative_cwd,
                    timeout,
                    capsule.limits.max_output_chars,
                    container_runtime,
                )
            else:
                outcome = _execute_local_command(
                    check,
                    check_dir,
                    timeout,
                    capsule.limits.max_output_chars,
                )
        except SandboxError as exc:
            status = CheckStatus.ERROR
            outcome = _ProcessOutcome(
                exit_code=None,
                timed_out=False,
                stdout="",
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
                error=str(exc),
            )
            failure = sandbox_failure()
            infrastructure_error = True
        except OSError as exc:
            status = CheckStatus.ERROR
            outcome = _ProcessOutcome(
                exit_code=None,
                timed_out=False,
                stdout="",
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
                error=str(exc),
            )
            failure = launch_failure(exc, selected_backend.value)
            infrastructure_error = True
        else:
            if outcome.timed_out:
                status = CheckStatus.TIMED_OUT
                failure = timeout_failure(
                    timeout,
                    selected_backend.value,
                    infrastructure_code=outcome.failure_code,
                )
                if failure.impact is FailureImpact.INFRASTRUCTURE_ERROR:
                    infrastructure_error = True
            elif outcome.error is not None:
                status = CheckStatus.ERROR
                failure = output_capture_failure(selected_backend.value)
                infrastructure_error = True
            elif outcome.exit_code in check.expected_exit_codes:
                status = CheckStatus.PASSED
            else:
                status = CheckStatus.FAILED
                if outcome.exit_code is None:
                    raise RunnerError("completed command is missing an exit code")
                failure = unexpected_exit_failure(
                    outcome.exit_code,
                    sorted(check.expected_exit_codes),
                )

        result = CommandResult(
            id=check.id,
            argv=check.argv,
            cwd=relative_cwd,
            status=status,
            exit_code=outcome.exit_code,
            duration_seconds=monotonic() - command_started,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            stdout_truncated=outcome.stdout_truncated,
            stderr_truncated=outcome.stderr_truncated,
            error=outcome.error,
            failure=failure,
        )
        results.append(result)
        if status is not CheckStatus.PASSED:
            blocked_by_check_id = check.id
            halt = not check.continue_on_failure

    failed = any(result.status is not CheckStatus.PASSED for result in results)
    if infrastructure_error:
        run_status = RunStatus.ERROR
    elif failed:
        run_status = RunStatus.FAILED
    else:
        run_status = RunStatus.PASSED
    finished_at = datetime.now(UTC)
    return RunResult(
        capsule_id=capsule.id,
        status=run_status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=monotonic() - started_clock,
        checks=results,
        failure_summary=summarize_failures((result.id, result.failure) for result in results),
    )