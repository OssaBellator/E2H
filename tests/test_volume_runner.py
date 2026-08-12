from __future__ import annotations

import io
import json
import sys
import tarfile
from pathlib import Path
from typing import Any

import pytest

import e2h.volume_runner as volume_runner
from e2h.failures import FailureCode
from e2h.models import (
    CommandCheck,
    ContainerSandbox,
    InitialState,
    SuccessSpec,
    TaskCapsule,
)
from e2h.runner import CheckStatus, RunStatus, _ProcessOutcome
from e2h.volume_runner import run_capsule_prepared_volume
from e2h.workspace_archive import WorkspaceArchive

IMAGE = "python@sha256:" + "0" * 64


def _archive(
    *,
    directories: list[str],
    symlinks: dict[str, str] | None = None,
) -> WorkspaceArchive:
    symlinks = symlinks or {}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        for name in directories:
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            handle.addfile(member)
        for name, target in sorted(symlinks.items()):
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            handle.addfile(member)
    archive_bytes = stream.tell()
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset(directories),
        source_bytes=sum(
            len(target.encode("utf-8", errors="surrogateescape"))
            for target in symlinks.values()
        ),
        entries=len(directories) - 1 + len(symlinks),
        archive_bytes=archive_bytes,
    )


def _capsule(
    commands: list[CommandCheck],
    *,
    working_directory: str = ".",
) -> TaskCapsule:
    return TaskCapsule(
        id="prepared-volume",
        goal="Run checks against a prepared volume.",
        initial_state=InitialState(working_directory=working_directory),
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=commands),
    )


def _fake_runtime(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "docker-test"
    log = tmp_path / "docker-log.jsonl"
    runtime.write_text(
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
log = Path(os.environ["DOCKER_TEST_LOG"])
state_file = Path(str(log) + ".state")
with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args and args[0] == "inspect":
    if os.environ.get("DOCKER_TEST_INSPECT_FAIL"):
        print("inspect failed", file=sys.stderr)
        raise SystemExit(8)
    state = json.loads(state_file.read_text(encoding="utf-8"))
    if value := os.environ.get("DOCKER_TEST_INSPECT_STATUS"):
        state["Status"] = value
        state["Running"] = value == "running"
    if value := os.environ.get("DOCKER_TEST_INSPECT_EXIT"):
        state["ExitCode"] = int(value)
    if value := os.environ.get("DOCKER_TEST_INSPECT_ERROR"):
        state["Error"] = value
    print(json.dumps(state, sort_keys=True))
    raise SystemExit(0)
if args and args[0] == "rm":
    state_file.unlink(missing_ok=True)
    raise SystemExit(0)
command = args[-1] if args else ""
exit_code = 0
if command.startswith("docker-exit-"):
    exit_code = int(command.removeprefix("docker-exit-"))
elif command == "fail":
    exit_code = 7
state_file.write_text(
    json.dumps(
        {{
            "Status": "exited",
            "Running": False,
            "ExitCode": exit_code,
            "Error": "",
        }},
        sort_keys=True,
    ),
    encoding="utf-8",
)
print(f"executed:{{command}}")
raise SystemExit(exit_code)
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def _records(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _run_records(log: Path) -> list[list[str]]:
    return [record for record in _records(log) if record and record[0] == "run"]


def test_prepared_volume_runner_resolves_symlinked_workdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    archive = _archive(
        directories=[".", "task", "shared", "shared/nested"],
        symlinks={"task/link": "../shared"},
    )
    capsule = _capsule(
        [CommandCheck(id="check", argv=["ok"], cwd="link/nested")],
        working_directory="task",
    )

    result = run_capsule_prepared_volume(
        capsule,
        archive,
        "e2h-replay-workspace-abc",
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.PASSED
    assert result.checks[0].status is CheckStatus.PASSED
    assert result.checks[0].cwd == "shared/nested"
    assert result.checks[0].stdout == "executed:ok\n"
    records = _records(log)
    assert [record[0] for record in records] == ["run", "inspect", "rm"]
    args = records[0]
    assert "--rm" not in args
    assert "--cidfile" not in args
    assert "--name" in args
    assert args[args.index("--workdir") + 1] == "/workspace/shared/nested"
    assert args[args.index("--mount") + 1] == (
        "type=volume,src=e2h-replay-workspace-abc,dst=/workspace,"
        "volume-nocopy,readonly"
    )
    name = args[args.index("--name") + 1]
    assert records[1][-1] == name
    assert records[2] == ["rm", "-f", name]


def test_prepared_volume_runner_missing_cwd_fails_before_runtime(tmp_path: Path) -> None:
    archive = _archive(directories=[".", "task"])
    capsule = _capsule(
        [CommandCheck(id="check", argv=["never"], cwd="missing")],
        working_directory="task",
    )

    result = run_capsule_prepared_volume(
        capsule,
        archive,
        "e2h-replay-workspace-abc",
        container_runtime=str(tmp_path / "does-not-exist"),
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.WORKING_DIRECTORY_MISSING


def test_prepared_volume_runner_preserves_continue_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    archive = _archive(directories=["."])
    capsule = _capsule(
        [
            CommandCheck(id="first", argv=["fail"], continue_on_failure=True),
            CommandCheck(id="second", argv=["pass"]),
        ]
    )

    result = run_capsule_prepared_volume(
        capsule,
        archive,
        "e2h-replay-workspace-abc",
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.FAILED
    assert [check.status for check in result.checks] == [
        CheckStatus.FAILED,
        CheckStatus.PASSED,
    ]
    assert [record[-1] for record in _run_records(log)] == ["fail", "pass"]


@pytest.mark.parametrize("exit_code", [125, 126, 127])
def test_prepared_volume_runner_preserves_expected_ambiguous_docker_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
) -> None:
    runtime, log = _fake_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    archive = _archive(directories=["."])
    capsule = _capsule(
        [
            CommandCheck(
                id="ambiguous-exit",
                argv=[f"docker-exit-{exit_code}"],
                expected_exit_codes={exit_code},
            )
        ]
    )

    result = run_capsule_prepared_volume(
        capsule,
        archive,
        "e2h-replay-workspace-abc",
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.PASSED
    assert result.checks[0].status is CheckStatus.PASSED
    assert result.checks[0].exit_code == exit_code
    assert result.checks[0].failure is None


def test_prepared_volume_runner_rejects_nonexited_container_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_INSPECT_STATUS", "created")
    archive = _archive(directories=["."])

    result = run_capsule_prepared_volume(
        _capsule([CommandCheck(id="check", argv=["ok"])]),
        archive,
        "e2h-replay-workspace-abc",
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_RUNTIME
    assert "stable exited state" in (result.checks[0].error or "")
    assert _records(log)[-1][0] == "rm"


def test_prepared_volume_runner_rejects_inspected_exit_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_INSPECT_EXIT", "9")
    archive = _archive(directories=["."])

    result = run_capsule_prepared_volume(
        _capsule([CommandCheck(id="check", argv=["ok"])]),
        archive,
        "e2h-replay-workspace-abc",
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_RUNTIME
    assert "does not match inspected" in (result.checks[0].error or "")


def test_prepared_volume_runner_rejects_runtime_state_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_runtime(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_INSPECT_ERROR", "start failed")
    archive = _archive(directories=["."])

    result = run_capsule_prepared_volume(
        _capsule([CommandCheck(id="check", argv=["ok"])]),
        archive,
        "e2h-replay-workspace-abc",
        container_runtime=str(runtime),
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_RUNTIME
    assert "start failed" in (result.checks[0].error or "")


def test_prepared_volume_timeout_uses_generated_name_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(directories=["."])
    capsule = _capsule([CommandCheck(id="timeout", argv=["slow"])])
    observed: dict[str, Any] = {}

    def fake_execute(
        argv: list[str],
        cwd: Path,
        env: dict[str, str],
        timeout: float,
        max_output_chars: int,
    ) -> _ProcessOutcome:
        del cwd, env, timeout, max_output_chars
        observed["argv"] = argv
        return _ProcessOutcome(
            exit_code=None,
            timed_out=True,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        )

    def fake_cleanup(runtime: str, container_name: str) -> None:
        observed["runtime"] = runtime
        observed["cleanup_name"] = container_name
        return None

    monkeypatch.setattr(volume_runner, "_execute_process", fake_execute)
    monkeypatch.setattr(volume_runner, "force_remove_named_container", fake_cleanup)

    result = run_capsule_prepared_volume(
        capsule,
        archive,
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.FAILED
    assert result.checks[0].status is CheckStatus.TIMED_OUT
    argv = observed["argv"]
    assert isinstance(argv, list)
    assert "--rm" not in argv
    assert "--cidfile" not in argv
    name = argv[argv.index("--name") + 1]
    assert observed["cleanup_name"] == name
    assert str(name).startswith("e2h-replay-check-")


def test_prepared_volume_timeout_cleanup_failure_is_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(directories=["."])
    capsule = _capsule([CommandCheck(id="timeout", argv=["slow"])])

    monkeypatch.setattr(
        volume_runner,
        "_execute_process",
        lambda *args, **kwargs: _ProcessOutcome(
            exit_code=None,
            timed_out=True,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )
    monkeypatch.setattr(
        volume_runner,
        "force_remove_named_container",
        lambda *args, **kwargs: "cleanup failed",
    )

    result = run_capsule_prepared_volume(
        capsule,
        archive,
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.TIMED_OUT
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_CLEANUP
    assert result.checks[0].error == "cleanup failed"


def test_completed_container_cleanup_failure_is_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(directories=["."])
    capsule = _capsule([CommandCheck(id="check", argv=["ok"])])

    monkeypatch.setattr(
        volume_runner,
        "_execute_process",
        lambda *args, **kwargs: _ProcessOutcome(
            exit_code=0,
            timed_out=False,
            stdout="ok\n",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        ),
    )
    monkeypatch.setattr(
        volume_runner,
        "_inspect_named_container_state",
        lambda *args, **kwargs: volume_runner._DockerContainerState(
            status="exited",
            running=False,
            exit_code=0,
            error="",
        ),
    )
    monkeypatch.setattr(
        volume_runner,
        "force_remove_named_container",
        lambda *args, **kwargs: "cleanup failed",
    )

    result = run_capsule_prepared_volume(
        capsule,
        archive,
        "e2h-replay-workspace-abc",
        container_runtime="docker-test",
    )

    assert result.status is RunStatus.ERROR
    assert result.checks[0].status is CheckStatus.ERROR
    assert result.checks[0].failure is not None
    assert result.checks[0].failure.code is FailureCode.SANDBOX_CLEANUP
    assert "cleanup failed" in (result.checks[0].error or "")


def test_workspace_tree_rejects_archive_directory_metadata_mismatch() -> None:
    archive = _archive(directories=[".", "task"])
    forged = WorkspaceArchive(
        file=archive.file,
        directories=frozenset({"."}),
        source_bytes=archive.source_bytes,
        entries=archive.entries,
        archive_bytes=archive.archive_bytes,
    )

    with pytest.raises(volume_runner.RunnerError, match="metadata does not match"):
        run_capsule_prepared_volume(
            _capsule([CommandCheck(id="check", argv=["never"])]),
            forged,
            "e2h-replay-workspace-abc",
            container_runtime="does-not-exist",
        )


def test_workspace_tree_rejects_archive_entry_count_metadata_mismatch() -> None:
    archive = _archive(directories=[".", "task"])
    forged = WorkspaceArchive(
        file=archive.file,
        directories=archive.directories,
        source_bytes=archive.source_bytes,
        entries=archive.entries + 1,
        archive_bytes=archive.archive_bytes,
    )

    with pytest.raises(volume_runner.RunnerError, match="capture metadata does not match"):
        run_capsule_prepared_volume(
            _capsule([CommandCheck(id="check", argv=["never"])]),
            forged,
            "e2h-replay-workspace-abc",
            container_runtime="does-not-exist",
        )


def test_workspace_tree_rejects_archive_source_byte_metadata_mismatch() -> None:
    archive = _archive(
        directories=[".", "task"],
        symlinks={"task/link": "."},
    )
    forged = WorkspaceArchive(
        file=archive.file,
        directories=archive.directories,
        source_bytes=archive.source_bytes + 1,
        entries=archive.entries,
        archive_bytes=archive.archive_bytes,
    )

    with pytest.raises(volume_runner.RunnerError, match="capture metadata does not match"):
        run_capsule_prepared_volume(
            _capsule([CommandCheck(id="check", argv=["never"])]),
            forged,
            "e2h-replay-workspace-abc",
            container_runtime="does-not-exist",
        )


def test_workspace_tree_rejects_workdir_symlink_loop() -> None:
    archive = _archive(
        directories=["."],
        symlinks={"loop": "loop"},
    )
    capsule = _capsule(
        [CommandCheck(id="check", argv=["never"])],
        working_directory="loop",
    )

    with pytest.raises(volume_runner.RunnerError, match="symlink loop"):
        run_capsule_prepared_volume(
            capsule,
            archive,
            "e2h-replay-workspace-abc",
            container_runtime="does-not-exist",
        )
