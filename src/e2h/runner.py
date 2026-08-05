"""Deterministic local replay runner for E2H task capsules."""

from __future__ import annotations

import os
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from threading import Thread
from time import monotonic, sleep
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, Field

from e2h.models import CommandCheck, TaskCapsule

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


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capsule_id: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(ge=0)
    checks: list[CommandResult]


class RunnerError(RuntimeError):
    """Raised when a replay cannot be safely started."""


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


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
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


def _execute_command(
    check: CommandCheck,
    cwd: Path,
    timeout: float,
    max_output_chars: int,
) -> _ProcessOutcome:
    process = subprocess.Popen(
        check.argv,
        cwd=cwd,
        env={**os.environ, **check.env},
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

    return _ProcessOutcome(
        exit_code=None if timed_out else process.returncode,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        error="; ".join(capture_errors) or None,
    )


def _skipped(check: CommandCheck, cwd: str) -> CommandResult:
    return CommandResult(
        id=check.id,
        argv=check.argv,
        cwd=cwd,
        status=CheckStatus.SKIPPED,
        duration_seconds=0,
        error="skipped after an earlier check failed",
    )


def run_capsule(capsule: TaskCapsule, workspace: Path) -> RunResult:
    """Execute all command checks in a capsule inside a bounded workspace."""
    started_at = datetime.now(UTC)
    started_clock = monotonic()
    workspace_root = workspace.resolve()
    if not workspace_root.is_dir():
        raise RunnerError(f"workspace is not a directory: {workspace}")

    task_root = _safe_child(workspace_root, capsule.initial_state.working_directory)
    if not task_root.is_dir():
        raise RunnerError(f"working directory does not exist: {task_root}")

    results: list[CommandResult] = []
    halt = False
    infrastructure_error = False

    for check in capsule.success.commands:
        check_dir = _safe_child(task_root, check.cwd)
        relative_cwd = str(check_dir.relative_to(workspace_root)) or "."
        if halt:
            results.append(_skipped(check, relative_cwd))
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
                )
            )
            infrastructure_error = True
            halt = not check.continue_on_failure
            continue

        timeout = check.timeout_seconds or capsule.limits.default_timeout_seconds
        command_started = monotonic()
        try:
            outcome = _execute_command(
                check,
                check_dir,
                timeout,
                capsule.limits.max_output_chars,
            )
        except OSError as exc:
            results.append(
                CommandResult(
                    id=check.id,
                    argv=check.argv,
                    cwd=relative_cwd,
                    status=CheckStatus.ERROR,
                    duration_seconds=monotonic() - command_started,
                    error=str(exc),
                )
            )
            infrastructure_error = True
            halt = not check.continue_on_failure
            continue

        if outcome.timed_out:
            status = CheckStatus.TIMED_OUT
            error = f"command exceeded {timeout:g} seconds"
        elif outcome.error is not None:
            status = CheckStatus.ERROR
            error = f"unable to capture command output: {outcome.error}"
            infrastructure_error = True
        elif outcome.exit_code in check.expected_exit_codes:
            status = CheckStatus.PASSED
            error = None
        else:
            status = CheckStatus.FAILED
            error = None

        results.append(
            CommandResult(
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
                error=error,
            )
        )
        if status is not CheckStatus.PASSED:
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
    )
