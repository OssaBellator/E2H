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
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        for name in directories:
            member = tarfile.TarInfo(name)
            member.type = tarfile.DIRTYPE
            handle.addfile(member)
        for name, target in sorted((symlinks or {}).items()):
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            handle.addfile(member)
    archive_bytes = stream.tell()
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset(directories),
        source_bytes=0,
        entries=len(directories) - 1 + len(symlinks or {}),
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
with Path(os.environ["DOCKER_TEST_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args and args[0] == "rm":
    raise SystemExit(0)
command = args[-1] if args else ""
print(f"executed:{{command}}")
raise SystemExit(7 if command == "fail" else 0)
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def _records(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


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
    args = _records(log)[0]
    assert args[0] == "run"
    assert "--cidfile" not in args
    assert "--name" in args
    assert args[args.index("--workdir") + 1] == "/workspace/shared/nested"
    assert args[args.index("--mount") + 1] == (
        "type=volume,src=e2h-replay-workspace-abc,dst=/workspace,"
        "volume-nocopy,readonly"
    )


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
    assert [record[-1] for record in _records(log)] == ["fail", "pass"]


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
