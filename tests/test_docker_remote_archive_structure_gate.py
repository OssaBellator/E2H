from __future__ import annotations

import io
import os
import tarfile
from collections.abc import Callable
from typing import BinaryIO

import pytest

import e2h.docker_remote as docker_remote
from e2h.docker_remote import DockerRemoteError, prepared_workspace_volume
from e2h.models import ContainerSandbox
from e2h.workspace_archive import WorkspaceArchive

IMAGE = "python@sha256:" + "0" * 64


class _DescriptorWrapper:
    """Expose a real descriptor through a non-producer Python stream object."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle

    def fileno(self) -> int:
        return self._handle.fileno()

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return self._handle.seek(offset, whence)

    def read(self, size: int = -1) -> bytes:
        return self._handle.read(size)

    def tell(self) -> int:
        return self._handle.tell()

    def close(self) -> None:
        self._handle.close()


def _require_seal_support() -> None:
    if docker_remote.fcntl is None or not hasattr(os, "memfd_create"):
        pytest.skip("sealed memfd archive test requires Linux file seals")
    required = (
        hasattr(os, "MFD_CLOEXEC")
        and hasattr(os, "MFD_ALLOW_SEALING")
        and all(
            hasattr(docker_remote.fcntl, name)
            for name in (
                "F_ADD_SEALS",
                "F_GET_SEALS",
                "F_SEAL_WRITE",
                "F_SEAL_GROW",
                "F_SEAL_SHRINK",
                "F_SEAL_SEAL",
            )
        )
    )
    if not required:
        pytest.skip("sealed memfd archive test requires Linux file seals")


def _sealed_archive(
    write_members: Callable[[tarfile.TarFile], None],
    *,
    archive_format: int = tarfile.PAX_FORMAT,
) -> WorkspaceArchive:
    _require_seal_support()
    assert docker_remote.fcntl is not None
    descriptor = os.memfd_create(
        "e2h-test-forged-archive",
        flags=os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
    )
    stream = os.fdopen(descriptor, "w+b", buffering=0)
    with tarfile.open(fileobj=stream, mode="w", format=archive_format) as handle:
        write_members(handle)
    archive_bytes = stream.tell()
    seals = (
        docker_remote.fcntl.F_SEAL_WRITE
        | docker_remote.fcntl.F_SEAL_GROW
        | docker_remote.fcntl.F_SEAL_SHRINK
        | docker_remote.fcntl.F_SEAL_SEAL
    )
    docker_remote.fcntl.fcntl(stream.fileno(), docker_remote.fcntl.F_ADD_SEALS, seals)
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset({"."}),
        source_bytes=0,
        entries=1,
        archive_bytes=archive_bytes,
    )


def _sealed_gnu_long_name_archive() -> WorkspaceArchive:
    def write_members(handle: tarfile.TarFile) -> None:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        payload = tarfile.TarInfo("n" * 150)
        payload.size = 0
        handle.addfile(payload, io.BytesIO())

    return _sealed_archive(write_members, archive_format=tarfile.GNU_FORMAT)


def _sealed_missing_mtime_archive() -> WorkspaceArchive:
    def write_members(handle: tarfile.TarFile) -> None:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        payload = tarfile.TarInfo("payload.txt")
        payload.type = tarfile.REGTYPE
        payload.size = 0
        handle.addfile(payload, io.BytesIO())

    return _sealed_archive(write_members)


def _sealed_alternate_regular_type_archive() -> WorkspaceArchive:
    def write_members(handle: tarfile.TarFile) -> None:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.pax_headers["mtime"] = "0"
        handle.addfile(root)
        payload = tarfile.TarInfo("payload.txt")
        payload.type = tarfile.AREGTYPE
        payload.pax_headers["mtime"] = "0"
        payload.size = 0
        handle.addfile(payload, io.BytesIO())

    return _sealed_archive(write_members)


def _sealed_producer_encoding_archive() -> WorkspaceArchive:
    def write_members(handle: tarfile.TarFile) -> None:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.pax_headers["mtime"] = "0"
        handle.addfile(root)
        payload = tarfile.TarInfo("payload.txt")
        payload.type = tarfile.REGTYPE
        payload.pax_headers["mtime"] = "0"
        payload.size = 0
        handle.addfile(payload, io.BytesIO())

    return _sealed_archive(write_members)


def _assert_rejected_before_docker(
    archive: WorkspaceArchive,
    monkeypatch: pytest.MonkeyPatch,
    *,
    message: str,
) -> None:
    docker_contacted = False

    def forbidden_docker(*args: object, **kwargs: object) -> str:
        nonlocal docker_contacted
        del args, kwargs
        docker_contacted = True
        raise AssertionError("invalid sealed archive must fail before Docker contact")

    monkeypatch.setattr(docker_remote, "_run_docker", forbidden_docker)
    monkeypatch.setattr(docker_remote, "require_patched_docker_archive", forbidden_docker)
    monkeypatch.setattr(docker_remote, "_require_volume_free_image", forbidden_docker)

    try:
        with pytest.raises(DockerRemoteError, match=message):
            with prepared_workspace_volume(ContainerSandbox(image=IMAGE), archive):
                raise AssertionError("invalid archive must not yield a Docker volume")
        assert docker_contacted is False
    finally:
        archive.file.close()


def test_importer_rejects_nonproducer_tar_shape_before_docker_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_docker(
        _sealed_gnu_long_name_archive(),
        monkeypatch,
        message="unsupported tar header type",
    )


def test_importer_rejects_archive_member_without_producer_pax_mtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_docker(
        _sealed_missing_mtime_archive(),
        monkeypatch,
        message="missing producer PAX mtime",
    )


def test_importer_rejects_alternate_regular_file_header_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_rejected_before_docker(
        _sealed_alternate_regular_type_archive(),
        monkeypatch,
        message="producer regular file type",
    )


def test_importer_rejects_nonproducer_file_object_even_with_sealed_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _sealed_producer_encoding_archive()
    wrapped = WorkspaceArchive(
        file=_DescriptorWrapper(archive.file),  # type: ignore[arg-type]
        directories=archive.directories,
        source_bytes=archive.source_bytes,
        entries=archive.entries,
        archive_bytes=archive.archive_bytes,
    )
    _assert_rejected_before_docker(
        wrapped,
        monkeypatch,
        message="unbuffered FileIO",
    )
