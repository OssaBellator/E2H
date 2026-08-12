from __future__ import annotations

import io
import os
import tarfile

import pytest

import e2h.docker_remote as docker_remote
from e2h.docker_remote import DockerRemoteError, prepared_workspace_volume
from e2h.models import ContainerSandbox
from e2h.workspace_archive import WorkspaceArchive

IMAGE = "python@sha256:" + "0" * 64


def _sealed_gnu_long_name_archive() -> WorkspaceArchive:
    if docker_remote.fcntl is None or not hasattr(os, "memfd_create"):
        pytest.skip("sealed memfd archive test requires Linux file seals")
    required = (
        hasattr(os, "MFD_ALLOW_SEALING")
        and all(
            hasattr(docker_remote.fcntl, name)
            for name in (
                "F_ADD_SEALS",
                "F_SEAL_WRITE",
                "F_SEAL_GROW",
                "F_SEAL_SHRINK",
                "F_SEAL_SEAL",
            )
        )
    )
    if not required:
        pytest.skip("sealed memfd archive test requires Linux file seals")

    descriptor = os.memfd_create(
        "e2h-test-forged-archive",
        flags=os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
    )
    stream = os.fdopen(descriptor, "w+b", buffering=0)
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.GNU_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        payload = tarfile.TarInfo("n" * 150)
        payload.size = 0
        handle.addfile(payload, io.BytesIO())
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


def test_importer_rejects_nonproducer_tar_shape_before_docker_contact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _sealed_gnu_long_name_archive()
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
        with pytest.raises(DockerRemoteError, match="unsupported tar header type"):
            with prepared_workspace_volume(ContainerSandbox(image=IMAGE), archive):
                raise AssertionError("invalid archive must not yield a Docker volume")
        assert docker_contacted is False
    finally:
        archive.file.close()
