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


def _escaping_symlink_archive() -> WorkspaceArchive:
    target = "../outside"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        link = tarfile.TarInfo("escape")
        link.type = tarfile.SYMTYPE
        link.linkname = target
        handle.addfile(link)
    archive_bytes = stream.tell()
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset({"."}),
        source_bytes=len(target.encode("utf-8")),
        entries=1,
        archive_bytes=archive_bytes,
    )


def _symlink_parent_archive(*, child_first: bool) -> WorkspaceArchive:
    target = "."
    payload = b"x"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)

        link = tarfile.TarInfo("alias")
        link.type = tarfile.SYMTYPE
        link.linkname = target

        child = tarfile.TarInfo("alias/payload.txt")
        child.size = len(payload)

        if child_first:
            handle.addfile(child, io.BytesIO(payload))
            handle.addfile(link)
        else:
            handle.addfile(link)
            handle.addfile(child, io.BytesIO(payload))

    archive_bytes = stream.tell()
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset({"."}),
        source_bytes=len(target.encode("utf-8")) + len(payload),
        entries=2,
        archive_bytes=archive_bytes,
    )


def _capsule() -> TaskCapsule:
    return TaskCapsule(
        id="preimport-archive-validation",
        goal="Reject malformed sealed bytes before Docker import.",
        sandbox=ContainerSandbox(image=IMAGE),
        success=SuccessSpec(commands=[CommandCheck(id="check", argv=["check"])]),
    )


def _install_preflight_stubs(
    monkeypatch: pytest.MonkeyPatch,
    archive: WorkspaceArchive,
) -> list[bool]:
    imported = [False]

    @contextmanager
    def fake_capture(*args: object, **kwargs: object) -> Iterator[WorkspaceArchive]:
        del args, kwargs
        yield archive

    @contextmanager
    def forbidden_import(*args: object, **kwargs: object) -> Iterator[str]:
        del args, kwargs
        imported[0] = True
        raise AssertionError("Docker import must not receive an invalid archive")
        yield "unreachable"

    monkeypatch.setattr(isolated_runner, "_validated_remote_sandbox", lambda sandbox: sandbox)
    monkeypatch.setattr(isolated_runner, "require_patched_docker_archive", lambda runtime: None)
    monkeypatch.setattr(isolated_runner, "_require_volume_free_image", lambda runtime, image: None)
    monkeypatch.setattr(isolated_runner, "stable_workspace_archive", fake_capture)
    monkeypatch.setattr(isolated_runner, "prepared_workspace_volume", forbidden_import)
    return imported


def test_candidate_rejects_hidden_tar_payload_before_docker_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = _install_preflight_stubs(monkeypatch, _concatenated_archive())

    with pytest.raises(RunnerError, match="invalid trailer padding"):
        isolated_runner._run_capsule_isolated_container_candidate(
            _capsule(),
            tmp_path / "unused-workspace",
            max_workspace_bytes=1024 * 1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )

    assert imported == [False]


def test_candidate_rejects_escaping_symlink_before_docker_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = _install_preflight_stubs(monkeypatch, _escaping_symlink_archive())

    with pytest.raises(RunnerError, match="symlink escapes workspace root"):
        isolated_runner._run_capsule_isolated_container_candidate(
            _capsule(),
            tmp_path / "unused-workspace",
            max_workspace_bytes=1024 * 1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )

    assert imported == [False]


@pytest.mark.parametrize("child_first", [False, True])
def test_candidate_rejects_member_under_symlink_before_docker_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    child_first: bool,
) -> None:
    imported = _install_preflight_stubs(
        monkeypatch,
        _symlink_parent_archive(child_first=child_first),
    )

    with pytest.raises(RunnerError, match="nested under symlink 'alias'"):
        isolated_runner._run_capsule_isolated_container_candidate(
            _capsule(),
            tmp_path / "unused-workspace",
            max_workspace_bytes=1024 * 1024,
            max_workspace_entries=10,
            container_runtime="docker-test",
        )

    assert imported == [False]
