"""Remote container replay against a sealed-archive-backed Docker volume."""

from __future__ import annotations

import json
import os
import secrets
import tarfile
from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from time import monotonic

from e2h.failures import (
    FailureCategory,
    FailureCode,
    FailureImpact,
    FailureRecord,
    Retryability,
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
    _execute_process,
    _skipped,
    _validated_capsule,
)
from e2h.sandbox import (
    SandboxError,
    build_container_volume_argv,
    force_remove_named_container,
)
from e2h.workspace_archive import (
    _MAX_ARCHIVE_DEPTH,
    _MAX_ARCHIVE_MEMBER_PATH_BYTES,
    WorkspaceArchive,
)

_MAX_CWD_SYMLINK_RESOLUTIONS = 40
_CONTROL_OUTPUT_CHARS = 8192
_CONTROL_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class _WorkspaceTree:
    directories: frozenset[str]
    symlinks: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _DockerContainerState:
    status: str
    running: bool
    exit_code: int
    error: str


def _member_path(value: str) -> str:
    if not value or "\x00" in value:
        raise RunnerError("sealed workspace archive contains an invalid member path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise RunnerError("sealed workspace archive contains an unsafe member path")
    if len(path.parts) > _MAX_ARCHIVE_DEPTH:
        raise RunnerError("sealed workspace archive member exceeds capture depth")
    rendered = path.as_posix()
    if len(rendered.encode("utf-8", errors="surrogateescape")) > _MAX_ARCHIVE_MEMBER_PATH_BYTES:
        raise RunnerError("sealed workspace archive member exceeds capture path bound")
    return "." if rendered == "." else rendered


def _symlink_target(value: str) -> str:
    if not value or "\x00" in value:
        raise RunnerError("sealed workspace archive contains an invalid symlink target")
    if PurePosixPath(value).is_absolute():
        raise RunnerError("sealed workspace archive contains an absolute symlink target")
    return value


def _workspace_tree(archive: WorkspaceArchive) -> _WorkspaceTree:
    if type(archive) is not WorkspaceArchive:
        raise RunnerError(
            f"invalid workspace archive: expected WorkspaceArchive, got {type(archive).__name__}"
        )
    if archive.source_bytes < 0 or archive.entries < 0 or archive.archive_bytes < 1:
        raise RunnerError("sealed workspace archive metadata is invalid")
    expected_members = archive.entries + 1
    directories: set[str] = set()
    symlinks: dict[str, str] = {}
    seen: set[str] = set()
    source_bytes = 0
    try:
        archive.file.seek(0, os.SEEK_END)
        if archive.file.tell() != archive.archive_bytes:
            raise RunnerError("sealed workspace archive size does not match captured metadata")
        archive.file.seek(0)
        with tarfile.open(
            fileobj=archive.file,
            mode="r:",
            encoding="utf-8",
            errors="surrogateescape",
        ) as handle:
            for member in handle:
                if len(seen) >= expected_members:
                    raise RunnerError(
                        "sealed workspace archive capture metadata does not match archive bytes"
                    )
                name = _member_path(member.name)
                if name in seen:
                    raise RunnerError(
                        f"sealed workspace archive contains duplicate member {name!r}"
                    )
                seen.add(name)
                if member.isdir():
                    directories.add(name)
                elif member.issym():
                    target = _symlink_target(member.linkname)
                    symlinks[name] = target
                    source_bytes += len(target.encode("utf-8", errors="surrogateescape"))
                elif member.isfile():
                    if member.size < 0:
                        raise RunnerError("sealed workspace archive contains an invalid file size")
                    source_bytes += member.size
                else:
                    raise RunnerError(
                        f"sealed workspace archive contains unsupported member {name!r}"
                    )
                if source_bytes > archive.source_bytes:
                    raise RunnerError(
                        "sealed workspace archive capture metadata does not match archive bytes"
                    )
    except RunnerError:
        raise
    except (AttributeError, OSError, tarfile.TarError, UnicodeError, ValueError) as exc:
        raise RunnerError(f"unable to inspect sealed workspace archive: {exc}") from exc
    finally:
        try:
            archive.file.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
    if "." not in directories:
        raise RunnerError("sealed workspace archive is missing its root directory")
    if frozenset(directories) != archive.directories:
        raise RunnerError(
            "sealed workspace archive directory metadata does not match archive bytes"
        )
    if len(seen) != expected_members or source_bytes != archive.source_bytes:
        raise RunnerError(
            "sealed workspace archive capture metadata does not match archive bytes"
        )
    return _WorkspaceTree(
        directories=frozenset(directories),
        symlinks=tuple(sorted(symlinks.items())),
    )


def _relative_parts(value: str) -> list[str]:
    if "\x00" in value:
        raise RunnerError("workspace directory path must not contain NUL")
    path = PurePosixPath(value or ".")
    if path.is_absolute() or ".." in path.parts:
        raise RunnerError(f"unsafe workspace directory path: {value}")
    return [part for part in path.parts if part not in {"", "."}]


def _render_parts(parts: list[str]) -> str:
    return "." if not parts else PurePosixPath(*parts).as_posix()


def _resolve_directory(tree: _WorkspaceTree, base: str, relative: str) -> str | None:
    base_parts = _relative_parts(base)
    if _render_parts(base_parts) not in tree.directories:
        raise RunnerError(f"captured base directory is missing: {base}")
    pending = deque(_relative_parts(relative))
    resolved = list(base_parts)
    symlinks = dict(tree.symlinks)
    followed = 0
    while pending:
        part = pending.popleft()
        if part in {"", "."}:
            continue
        if part == "..":
            if not resolved:
                raise RunnerError("sealed workspace archive symlink escapes workspace root")
            resolved.pop()
            continue
        candidate = _render_parts([*resolved, part])
        target = symlinks.get(candidate)
        if target is not None:
            followed += 1
            if followed > _MAX_CWD_SYMLINK_RESOLUTIONS:
                raise RunnerError(
                    "sealed workspace archive contains a working-directory symlink loop"
                )
            target_parts = list(PurePosixPath(target).parts)
            pending.extendleft(reversed(target_parts))
            continue
        if candidate not in tree.directories:
            return None
        resolved.append(part)
    return _render_parts(resolved)


def _requested_relative_cwd(base: str, relative: str) -> str:
    parts = [*_relative_parts(base), *_relative_parts(relative)]
    return _render_parts(parts)


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


def _validate_remote_policy(capsule: TaskCapsule) -> None:
    sandbox = capsule.sandbox
    if sandbox is None:
        raise RunnerError("prepared-volume replay requires capsule.sandbox")
    if sandbox.workspace_access != "read_only":
        raise RunnerError("prepared-volume replay requires sandbox.workspace_access='read_only'")
    if not sandbox.read_only_root:
        raise RunnerError("prepared-volume replay requires sandbox.read_only_root=true")
    if sandbox.pull_policy != "never":
        raise RunnerError("prepared-volume replay requires sandbox.pull_policy='never'")


def _completed_cleanup_failure() -> FailureRecord:
    return FailureRecord(
        category=FailureCategory.SANDBOX,
        code=FailureCode.SANDBOX_CLEANUP,
        impact=FailureImpact.INFRASTRUCTURE_ERROR,
        retryability=Retryability.AFTER_FIX,
        summary="completed container could not be cleaned up safely",
        details={"backend": ExecutionBackend.CONTAINER.value},
    )


def _inspect_named_container_state(
    runtime: str,
    container_name: str,
) -> _DockerContainerState:
    outcome = _execute_process(
        [
            runtime,
            "inspect",
            "--type",
            "container",
            "--format",
            "{{json .State}}",
            container_name,
        ],
        Path("/"),
        os.environ.copy(),
        _CONTROL_TIMEOUT_SECONDS,
        _CONTROL_OUTPUT_CHARS,
    )
    if outcome.timed_out:
        raise SandboxError("Docker container state inspection timed out")
    if outcome.error is not None:
        raise SandboxError(f"unable to capture Docker container state: {outcome.error}")
    if outcome.exit_code != 0:
        detail = outcome.stderr.strip() or f"exit {outcome.exit_code}"
        raise SandboxError(f"unable to inspect Docker container state: {detail}")
    try:
        payload = json.loads(outcome.stdout)
    except (TypeError, ValueError) as exc:
        raise SandboxError("Docker container state inspection returned invalid JSON") from exc
    if type(payload) is not dict:
        raise SandboxError("Docker container state inspection returned an invalid object")
    status = payload.get("Status")
    running = payload.get("Running")
    exit_code = payload.get("ExitCode")
    error = payload.get("Error")
    if (
        type(status) is not str
        or type(running) is not bool
        or type(exit_code) is not int
        or type(error) is not str
    ):
        raise SandboxError("Docker container state inspection returned invalid fields")
    return _DockerContainerState(
        status=status,
        running=running,
        exit_code=exit_code,
        error=error,
    )


def _runtime_state_error(
    outcome: _ProcessOutcome,
    state: _DockerContainerState,
) -> str | None:
    if state.status != "exited" or state.running:
        return f"Docker container did not reach a stable exited state ({state.status})"
    if state.error:
        return f"Docker container reported a runtime error: {state.error}"
    if outcome.exit_code is None:
        return "Docker run completed without an exit status"
    if outcome.exit_code != state.exit_code:
        return (
            "Docker run exit status does not match inspected container state "
            f"({outcome.exit_code} != {state.exit_code})"
        )
    return None


def _execute_volume_command(
    capsule: TaskCapsule,
    check: CommandCheck,
    volume_name: str,
    relative_cwd: str,
    timeout: float,
    max_output_chars: int,
    runtime_binary: str | None,
) -> _ProcessOutcome:
    if capsule.sandbox is None:
        raise SandboxError("container execution requires capsule.sandbox")
    runtime = runtime_binary or capsule.sandbox.engine
    container_name = f"e2h-replay-check-{secrets.token_hex(16)}"
    argv = build_container_volume_argv(
        capsule,
        check,
        volume_name,
        relative_cwd,
        container_name,
        runtime_binary=runtime,
    )
    outcome = _execute_process(
        argv,
        Path("/"),
        os.environ.copy(),
        timeout,
        max_output_chars,
    )
    if outcome.timed_out:
        cleanup_error = force_remove_named_container(runtime, container_name)
        if cleanup_error is not None:
            combined = "; ".join(item for item in (outcome.error, cleanup_error) if item)
            outcome = replace(
                outcome,
                error=combined,
                failure_code=FailureCode.SANDBOX_CLEANUP,
            )
        return outcome

    state_error: str | None = None
    state: _DockerContainerState | None = None
    try:
        state = _inspect_named_container_state(runtime, container_name)
    except SandboxError as exc:
        state_error = str(exc)
    if state is not None:
        state_error = _runtime_state_error(outcome, state)
        if state_error is None:
            outcome = replace(outcome, exit_code=state.exit_code)

    cleanup_error = force_remove_named_container(runtime, container_name)
    if state_error is not None or cleanup_error is not None:
        combined = "; ".join(
            item for item in (outcome.error, state_error, cleanup_error) if item
        )
        outcome = replace(
            outcome,
            error=combined,
            failure_code=(
                FailureCode.SANDBOX_CLEANUP
                if cleanup_error is not None
                else FailureCode.SANDBOX_RUNTIME
            ),
        )
    return outcome


def run_capsule_prepared_volume(
    capsule: TaskCapsule,
    archive: WorkspaceArchive,
    volume_name: str,
    *,
    container_runtime: str | None = None,
) -> RunResult:
    """Run checks against a prepared Docker volume without a host workspace pathname."""
    capsule = _validated_capsule(capsule)
    _validate_remote_policy(capsule)
    tree = _workspace_tree(archive)
    task_cwd = _resolve_directory(tree, ".", capsule.initial_state.working_directory)
    if task_cwd is None:
        raise RunnerError(
            "working directory does not exist: "
            f"{capsule.initial_state.working_directory}"
        )

    started_at = datetime.now(UTC)
    started_clock = monotonic()
    results: list[CommandResult] = []
    halt = False
    blocked_by_check_id: str | None = None
    infrastructure_error = False

    for check in capsule.success.commands:
        requested_cwd = _requested_relative_cwd(task_cwd, check.cwd)
        if halt:
            if blocked_by_check_id is None:
                raise RunnerError("halted replay is missing its blocking check")
            skipped_cwd = _resolve_directory(tree, task_cwd, check.cwd) or requested_cwd
            results.append(_skipped(check, skipped_cwd, blocked_by_check_id))
            continue

        relative_cwd = _resolve_directory(tree, task_cwd, check.cwd)
        if relative_cwd is None:
            results.append(_missing_check_result(check, requested_cwd))
            infrastructure_error = True
            blocked_by_check_id = check.id
            halt = not check.continue_on_failure
            continue

        timeout = check.timeout_seconds or capsule.limits.default_timeout_seconds
        command_started = monotonic()
        failure: FailureRecord | None = None
        try:
            outcome = _execute_volume_command(
                capsule,
                check,
                volume_name,
                relative_cwd,
                timeout,
                capsule.limits.max_output_chars,
                container_runtime,
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
            failure = launch_failure(exc, ExecutionBackend.CONTAINER.value)
            infrastructure_error = True
        else:
            if outcome.timed_out:
                status = CheckStatus.TIMED_OUT
                failure = timeout_failure(
                    timeout,
                    ExecutionBackend.CONTAINER.value,
                    infrastructure_code=outcome.failure_code,
                )
                if failure.impact is FailureImpact.INFRASTRUCTURE_ERROR:
                    infrastructure_error = True
            elif outcome.error is not None:
                status = CheckStatus.ERROR
                if outcome.failure_code is FailureCode.SANDBOX_CLEANUP:
                    failure = _completed_cleanup_failure()
                elif outcome.failure_code is FailureCode.SANDBOX_RUNTIME:
                    failure = sandbox_failure()
                else:
                    failure = output_capture_failure(ExecutionBackend.CONTAINER.value)
                infrastructure_error = True
            elif outcome.exit_code is None:
                raise RunnerError("completed command is missing an exit code")
            elif outcome.exit_code in check.expected_exit_codes:
                status = CheckStatus.PASSED
            else:
                status = CheckStatus.FAILED
                failure = unexpected_exit_failure(
                    outcome.exit_code,
                    sorted(check.expected_exit_codes),
                )

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
                error=outcome.error,
                failure=failure,
            )
        )
        if status is not CheckStatus.PASSED:
            blocked_by_check_id = check.id
            halt = not check.continue_on_failure

    failed = any(result.status is not CheckStatus.PASSED for result in results)
    run_status = (
        RunStatus.ERROR
        if infrastructure_error
        else RunStatus.FAILED
        if failed
        else RunStatus.PASSED
    )
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
