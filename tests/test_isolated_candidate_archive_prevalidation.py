from __future__ import annotations

import io
import tarfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

import e2h.isolated_runner as isolated_runner
from e2h.models import CommandCheck, ContainerSandbox, SuccessSpec, TaskCapsule
from e2h.runner import RunnerError
from e2h.workspace_archive import WorkspaceArchive

IMAGE = "python@sha256:" + "0" * 64


def _root_tar() -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
    return stream.getvalue()


def _concatenated_archive() -> WorkspaceArchive:
    stream = io.BytesIO(_root_tar() + _root_tar())
    return WorkspaceArchive(
        file=stream,
        directories=frozenset({"."}),
        source_bytes=0,
        entries=0,
        archive_bytes=len(stream.getbuffer()),
    )


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="preimport-archive-validation",
        goal="Reject malformed sealed bytes before Docker import.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )


def test_candidate_rejects_hidden_tar_payload_before_docker_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _concatenated_archive()
    imported = False

    @contextmanager
    def fake_capture(*args: object, **kwargs: object) -> Iterator[WorkspaceArchive]:
        del args, kwargs
        yield archive

    @contextmanager
    def forbidden_import(*args: object, **kwargs: object) -> Iterator[str]:
        nonlocal imported
        del args, kwargs
        imported = True
        raise AssertionError("Docker import must not receive an invalid archive")
        yield "unreachable"

    monkeypatch.setattr(isolated_runner, "_validated_remote_sandbox", lambda sandbox: sandbox)
    monkeypatch.setattr(isolated_runner, "require_patched_docker_archive", lambda runtime: None)
    monkeypatch.setattr(isolated_runner, "_require_volume_free_image", lambda runtime, image: None)
    monkeypatch.setattr(isolated_runner, "stable_workspace_archive", fake_capture)
    monkeypatch.setattr(isolated_runner, "prepared_workspace_volume", forbidden_import)

    with pytest.raises(RunnerError, match="invalid trailer padding"):
        isolated_runner._run_capsule_isolated_container_candidate(
            _capsule(),
            tmp_path / "unused-workspace",
            max_workspace_bytes=1024 * 1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )

    assert imported is False
