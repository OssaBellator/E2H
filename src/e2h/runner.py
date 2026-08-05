"""Deterministic local replay runner for E2H task capsules."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field

from e2h.models import CommandCheck, TaskCapsule


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


def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RunnerError(f"path escapes workspace: {relative}") from exc
    return candidate


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    marker = "\n... <output truncated> ...\n"
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return f"{value[:head]}{marker}{value[-tail:] if tail else ''}", True


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
            completed = subprocess.run(
                check.argv,
                cwd=check_dir,
                env={**os.environ, **check.env},
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            stdout, stdout_truncated = _truncate(
                completed.stdout, capsule.limits.max_output_chars
            )
            stderr, stderr_truncated = _truncate(
                completed.stderr, capsule.limits.max_output_chars
            )
            status = (
                CheckStatus.PASSED
                if completed.returncode in check.expected_exit_codes
                else CheckStatus.FAILED
            )
            results.append(
                CommandResult(
                    id=check.id,
                    argv=check.argv,
                    cwd=relative_cwd,
                    status=status,
                    exit_code=completed.returncode,
                    duration_seconds=monotonic() - command_started,
                    stdout=stdout,
                    stderr=stderr,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                )
            )
            if status is not CheckStatus.PASSED:
                halt = not check.continue_on_failure
        except subprocess.TimeoutExpired as exc:
            stdout_raw = (
                exc.stdout.decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            stderr_raw = (
                exc.stderr.decode("utf-8", errors="replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or "")
            )
            stdout, stdout_truncated = _truncate(stdout_raw, capsule.limits.max_output_chars)
            stderr, stderr_truncated = _truncate(stderr_raw, capsule.limits.max_output_chars)
            results.append(
                CommandResult(
                    id=check.id,
                    argv=check.argv,
                    cwd=relative_cwd,
                    status=CheckStatus.TIMED_OUT,
                    duration_seconds=monotonic() - command_started,
                    stdout=stdout,
                    stderr=stderr,
                    stdout_truncated=stdout_truncated,
                    stderr_truncated=stderr_truncated,
                    error=f"command exceeded {timeout:g} seconds",
                )
            )
            halt = not check.continue_on_failure
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

    failed = any(result.status is not CheckStatus.PASSED for result in results)
    if infrastructure_error:
        status = RunStatus.ERROR
    elif failed:
        status = RunStatus.FAILED
    else:
        status = RunStatus.PASSED
    finished_at = datetime.now(UTC)
    return RunResult(
        capsule_id=capsule.id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=monotonic() - started_clock,
        checks=results,
    )
