"""Handle-bound replay for security-sensitive remote execution surfaces."""

from __future__ import annotations

import os
import stat
import sys
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from time import monotonic

from e2h.directory_binding import DirectoryBindingError, open_relative_directory
from e2h.failures import (
    FailureCode,
    FailureImpact,
    FailureRecord,
    launch_failure,
    output_capture_failure,
    sandbox_failure,
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
    _execute_process,
    _skipped,
    _validated_capsule,
)
from e2h.sandbox import SandboxError, build_container_argv, force_remove_container


def _proc_fd_directory(descriptor: int) -> Path:
    if not sys.platform.startswith("linux"):
        raise RunnerError("handle-bound MCP replay requires Linux procfs")
    path = Path(f"/proc/{os.getpid()}/fd/{descriptor}")
    if not path.exists():
        raise RunnerError("handle-bound MCP replay requires accessible Linux procfs")
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


def _execute_bound_container_command(
    capsule: TaskCapsule,
    check: CommandCheck,
    workspace: Path,
    *,
    workspace_descriptor: int,
    check_descriptor: int,
    relative_cwd: str,
    timeout: float,
    max_output_chars: int,
    runtime_override: str | None,
) -> _ProcessOutcome:
    sandbox = capsule.sandbox
    if sandbox is None:
        raise SandboxError("container backend requires capsule.sandbox")
    runtime = runtime_override or sandbox.engine
    workspace_source = str(_proc_fd_directory(workspace_descriptor))
    check_source = str(_proc_fd_directory(check_descriptor))
    with TemporaryDirectory(prefix="e2h-container-") as temporary:
        cidfile = Path(temporary) / "container.cid"
        argv = build_container_argv(
            capsule,
            check,
            workspace,
            relative_cwd,
            cidfile,
            runtime_binary=runtime,
            workspace_mount_source=workspace_source,
            working_directory_mount_source=check_source,
        )
        outcome = _execute_process(
            argv,
            _proc_fd_directory(workspace_descriptor),
            os.environ.copy(),
            timeout,
            max_output_chars,
        )
        if not outcome.timed_out:
            return outcome
        cleanup_error = force_remove_container(runtime, cidfile)
        if cleanup_error is None:
            return outcome
        combined_error = cleanup_error if outcome.error is None else f"{outcome.error}; {cleanup_error}"
        return replace(
            outcome,
            error=combined_error,
            failure_code=FailureCode.SANDBOX_CLEANUP,
        )


def _run_capsule_bound(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    workspace_descriptor: int,
    backend: ExecutionBackend,
    container_runtime: str | None = None,
) -> RunResult:
    capsule = _validated_capsule(capsule)
    if backend not in {ExecutionBackend.LOCAL, ExecutionBackend.CONTAINER}:
        raise RunnerError(f"unsupported bound replay backend: {backend.value}")
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
        for check in capsule.success.commands:
            requested_cwd = _requested_relative_cwd(
                capsule.initial_state.working_directory,
                check.cwd,
            )
            if halt:
                if blocked_by_check_id is None:
                    raise RunnerError("halted replay is missing its blocking check")
                results.append(_skipped(check, requested_cwd, blocked_by_check_id))
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
                if backend is ExecutionBackend.LOCAL:
                    outcome = _execute_local_command(
                        check,
                        _proc_fd_directory(check_descriptor),
                        timeout,
                        capsule.limits.max_output_chars,
                    )
                else:
                    outcome = _execute_bound_container_command(
                        capsule,
                        check,
                        workspace,
                        workspace_descriptor=workspace_descriptor,
                        check_descriptor=check_descriptor,
                        relative_cwd=relative_cwd,
                        timeout=timeout,
                        max_output_chars=capsule.limits.max_output_chars,
                        runtime_override=container_runtime,
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
                failure = sandbox_failure(configuration=capsule.sandbox is None)
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
                failure = launch_failure(exc, backend.value)
                infrastructure_error = True
            else:
                if outcome.timed_out:
                    status = CheckStatus.TIMED_OUT
                    failure = timeout_failure(
                        timeout,
                        backend.value,
                        infrastructure_code=outcome.failure_code,
                    )
                    if failure.impact is FailureImpact.INFRASTRUCTURE_ERROR:
                        infrastructure_error = True
                elif outcome.error is not None:
                    status = CheckStatus.ERROR
                    failure = output_capture_failure(backend.value)
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


def run_capsule_bound_local(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    workspace_descriptor: int,
) -> RunResult:
    """Run a capsule locally with command cwd bound to verified directory handles."""
    return _run_capsule_bound(
        capsule,
        workspace,
        workspace_descriptor=workspace_descriptor,
        backend=ExecutionBackend.LOCAL,
    )


def run_capsule_bound_container(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    workspace_descriptor: int,
    container_runtime: str | None = None,
) -> RunResult:
    """Run a container capsule with workspace/cwd bind sources backed by held handles."""
    return _run_capsule_bound(
        capsule,
        workspace,
        workspace_descriptor=workspace_descriptor,
        backend=ExecutionBackend.CONTAINER,
        container_runtime=container_runtime,
    )
