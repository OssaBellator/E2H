from __future__ import annotations

import io
import signal
import tarfile

import pytest

import e2h.volume_runner as volume_runner
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError, _ProcessOutcome
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


def test_signal_ambiguous_exit_halts_before_later_remote_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ambiguous = 128 + int(signal.SIGKILL)
    calls: list[str] = []

    def fake_execute(
        capsule: TaskCapsule,
        check: CommandCheck,
        volume_name: str,
        relative_cwd: str,
        timeout: float,
        max_output_chars: int,
        runtime_binary: str | None,
    ) -> _ProcessOutcome:
        del capsule, volume_name, relative_cwd, timeout, max_output_chars, runtime_binary
        calls.append(check.id)
        if check.id != "first":
            raise AssertionError("later remote checks must not run after ambiguous signal status")
        return _ProcessOutcome(
            exit_code=ambiguous,
            timed_out=False,
            stdout="",
            stderr="",
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(volume_runner, "_execute_volume_command", fake_execute)
    capsule = TaskCapsule(
        id="remote-signal-halt",
        goal="Reject signal-ambiguous Docker status immediately.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(
            commands=[
                CommandCheck(id="first", argv=["first"], continue_on_failure=True),
                CommandCheck(id="second", argv=["second"]),
            ]
        ),
    )

    with pytest.raises(RunnerError, match=rf"signal-ambiguous Docker exit status.*{ambiguous}"):
        volume_runner.run_capsule_prepared_volume(
            capsule,
            _archive(),
            "e2h-replay-workspace-abc",
            container_runtime="docker-test",
        )

    assert calls == ["first"]
