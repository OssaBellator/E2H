"""Handle-bound local replay for the MCP execution boundary."""

from __future__ import annotations

import os
import stat
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import monotonic

from e2h.directory_binding import (
    DirectoryBindingError,
    directory_binding_supported,
    open_relative_directory,
)
from e2h.failures import (
    FailureImpact,
    FailureRecord,
    launch_failure,
    output_capture_failure,
    summarize_failures,
    timeout_failure,
    unexpected_exit_failure,
    working_directory_failure,
)
from e2h.models import CommandCheck, TaskCapsule
from e2h.runner import (
    CheckStatus,
    CommandResult,
    ExecutionBackend,
    RunnerError,
    RunResult,
    RunStatus,
    _ProcessOutcome,
    _execute_local_command,
    _skipped,
    _validated_capsule,
)


def handle_bound_local_replay_supported() -> bool:
    """Return whether the current host can launch handle-bound local replay."""
    if not directory_binding_supported() or not sys.platform.startswith("linux"):
        return False
    return Path(f"/proc/{os.getpid()}/fd").is_dir()


def _proc_fd_directory(descriptor: int) -> Path:
    if not sys.platform.startswith("linux"):
        raise RunnerError("handle-bound MCP local replay requires Linux procfs")
    path = Path(f"/proc/{os.getpid()}/fd/{descriptor}")
    if not path.exists():
        raise RunnerError("handle-bound MCP local replay requires accessible Linux procfs")
    return path


def _requested_relative_cwd(working_directory: str, check_cwd: str) -> str:
    path = PurePosixPath(working_directory).joinpath(check_cwd)
    rendered = str(path)
    return "." if rendered == "." else rendered


def _bound_relative_cwd(workspace_descriptor: int, check_descriptor: int) -> str:
    try:
        workspace = _proc_fd_directory(workspace_descriptor).resolve(strict=True)
        check = _proc_fd_directory(check_descriptor).resolve(strict=True)
        relative = check.relative_to(workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        raise RunnerError(f"unable to resolve bound check directory: {exc}") from exc
    return relative.as_posix() or "."


def _missing_check_result(check: CommandCheck, relative_cwd: str) -> CommandResult:
    return CommandResult(
        id=check.id,
        argv=check.argv,
        cwd=relative_cwd,
        status=CheckStatus.ERROR,
        duration_seconds=0,
        error=f"check directory does not exist: {relative_cwd}",
        failure=working_directory_failure(),
    )


def run_capsule_bound_local(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    workspace_descriptor: int,
) -> RunResult:
    """Run a capsule locally with every command cwd bound to a verified directory handle."""
    capsule = _validated_capsule(capsule)
    started_at = datetime.now(UTC)
    started_clock = monotonic()
    try:
        workspace_info = os.fstat(workspace_descriptor)
    except OSError as exc:
        raise RunnerError(f"unable to inspect bound workspace: {exc}") from exc
    if not stat.S_ISDIR(workspace_info.st_mode):
        raise RunnerError(f"workspace is not a directory: {workspace}")
    _proc_fd_directory(workspace_descriptor)

    try:
        task_descriptor = open_relative_directory(
            workspace_descriptor,
            capsule.initial_state.working_directory,
            containment_descriptor=workspace_descriptor,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise RunnerError(
            "working directory does not exist: "
            f"{capsule.initial_state.working_directory}"
        ) from exc
    except DirectoryBindingError as exc:
        raise RunnerError(str(exc)) from exc
    except OSError as exc:
        raise RunnerError(f"unable to open working directory: {exc}") from exc

    results: list[CommandResult] = []
    halt = False
    blocked_by_check_id: str | None = None
    infrastructure_error = False
    try:
        task_cwd = _bound_relative_cwd(workspace_descriptor, task_descriptor)
        for check in capsule.success.commands:
            requested_cwd = _requested_relative_cwd(task_cwd, check.cwd)
            if halt:
                if blocked_by_check_id is None:
                    raise RunnerError("halted replay is missing its blocking check")
                skipped_cwd = requested_cwd
                try:
                    skipped_descriptor = open_relative_directory(
                        task_descriptor,
                        check.cwd,
                        containment_descriptor=workspace_descriptor,
                    )
                except (FileNotFoundError, NotADirectoryError, PermissionError):
                    pass
                except DirectoryBindingError as exc:
                    raise RunnerError(str(exc)) from exc
                except OSError as exc:
                    raise RunnerError(f"unable to open skipped check directory: {exc}") from exc
                else:
                    try:
                        skipped_cwd = _bound_relative_cwd(
                            workspace_descriptor,
                            skipped_descriptor,
                        )
                    finally:
                        with suppress(OSError):
                            os.close(skipped_descriptor)
                results.append(_skipped(check, skipped_cwd, blocked_by_check_id))
                continue

            try:
                check_descriptor = open_relative_directory(
                    task_descriptor,
                    check.cwd,
                    containment_descriptor=workspace_descriptor,
                )
            except (FileNotFoundError, NotADirectoryError, PermissionError):
                results.append(_missing_check_result(check, requested_cwd))
                infrastructure_error = True
                blocked_by_check_id = check.id
                halt = not check.continue_on_failure
                continue
            except DirectoryBindingError as exc:
                raise RunnerError(str(exc)) from exc
            except OSError as exc:
                raise RunnerError(f"unable to open check directory: {exc}") from exc

            timeout = check.timeout_seconds or capsule.limits.default_timeout_seconds
            command_started = monotonic()
            failure: FailureRecord | None = None
            try:
                relative_cwd = _bound_relative_cwd(workspace_descriptor, check_descriptor)
                outcome = _execute_local_command(
                    check,
                    _proc_fd_directory(check_descriptor),
                    timeout,
                    capsule.limits.max_output_chars,
                )
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
                failure = launch_failure(exc, ExecutionBackend.LOCAL.value)
                infrastructure_error = True
            else:
                if outcome.timed_out:
                    status = CheckStatus.TIMED_OUT
                    failure = timeout_failure(
                        timeout,
                        ExecutionBackend.LOCAL.value,
                        infrastructure_code=outcome.failure_code,
                    )
                    if failure.impact is FailureImpact.INFRASTRUCTURE_ERROR:
                        infrastructure_error = True
                elif outcome.error is not None:
                    status = CheckStatus.ERROR
                    failure = output_capture_failure(ExecutionBackend.LOCAL.value)
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
            finally:
                with suppress(OSError):
                    os.close(check_descriptor)

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
    finally:
        with suppress(OSError):
            os.close(task_descriptor)

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
