from __future__ import annotations

import io
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import e2h.isolated_runner as isolated_runner
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError
from e2h.workspace_archive import WorkspaceArchive

IMAGE = "python@sha256:" + "0" * 64


def _gnu_long_name_archive() -> WorkspaceArchive:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.GNU_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        payload = tarfile.TarInfo("n" * 150)
        payload.size = 0
        handle.addfile(payload)
    archive_bytes = stream.tell()
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset({"."}),
        source_bytes=0,
        entries=1,
        archive_bytes=archive_bytes,
    )


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="raw-header-order",
        goal="Reject non-producer tar extensions before logical tar parsing.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )


def test_candidate_rejects_raw_extension_before_workspace_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _gnu_long_name_archive()

    @contextmanager
    def fake_capture(*args: object, **kwargs: object) -> Iterator[WorkspaceArchive]:
        del args, kwargs
        yield archive

    def forbidden_workspace_tree(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("generic tar parsing must not run before raw header rejection")

    monkeypatch.setattr(isolated_runner, "_validated_remote_sandbox", lambda sandbox: sandbox)
    monkeypatch.setattr(isolated_runner, "require_patched_docker_archive", lambda runtime: None)
    monkeypatch.setattr(isolated_runner, "_require_volume_free_image", lambda runtime, image: None)
    monkeypatch.setattr(isolated_runner, "stable_workspace_archive", fake_capture)
    monkeypatch.setattr(isolated_runner, "_workspace_tree", forbidden_workspace_tree)

    with pytest.raises(RunnerError, match="unsupported tar header type"):
        isolated_runner._run_capsule_isolated_container_candidate(
            _capsule(),
            tmp_path / "unused-workspace",
            max_workspace_bytes=1024 * 1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )
