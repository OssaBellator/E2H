from __future__ import annotations

import io
import tarfile

import pytest

from e2h.isolated_runner import _validate_archive_member_ancestry
from e2h.runner import RunnerError
from e2h.workspace_archive import WorkspaceArchive


def _archive(
    *,
    format: int,
    member_name: str = "payload.txt",
    symlink_target: str | None = None,
) -> WorkspaceArchive:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=format) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)

        member = tarfile.TarInfo(member_name)
        if symlink_target is None:
            member.size = 0
        else:
            member.type = tarfile.SYMTYPE
            member.linkname = symlink_target
            member.size = 0
        handle.addfile(member)

    archive_bytes = stream.tell()
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset({"."}),
        source_bytes=(
            0
            if symlink_target is None
            else len(symlink_target.encode("utf-8", errors="surrogateescape"))
        ),
        entries=1,
        archive_bytes=archive_bytes,
    )


def _pax_size_override_archive() -> WorkspaceArchive:
    root = tarfile.TarInfo(".")
    root.type = tarfile.DIRTYPE
    root_header = root.tobuf(
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="surrogateescape",
    )

    payload = tarfile.TarInfo("payload.txt")
    payload.size = 0
    payload.pax_headers["size"] = "1"
    payload_headers = payload.tobuf(
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="surrogateescape",
    )

    data_block = b"Z" + b"\0" * (tarfile.BLOCKSIZE - 1)
    trailer = b"\0" * (2 * tarfile.BLOCKSIZE)
    stream = io.BytesIO(root_header + payload_headers + data_block + trailer)
    return WorkspaceArchive(
        file=stream,
        directories=frozenset({"."}),
        source_bytes=1,
        entries=1,
        archive_bytes=len(stream.getbuffer()),
    )


def test_archive_prevalidation_rejects_gnu_long_name_extension() -> None:
    archive = _archive(format=tarfile.GNU_FORMAT, member_name="n" * 150)

    with pytest.raises(RunnerError, match="unsupported tar header type"):
        _validate_archive_member_ancestry(archive)


def test_archive_prevalidation_rejects_gnu_long_link_extension() -> None:
    archive = _archive(
        format=tarfile.GNU_FORMAT,
        member_name="link",
        symlink_target="t" * 150,
    )

    with pytest.raises(RunnerError, match="unsupported tar header type"):
        _validate_archive_member_ancestry(archive)


def test_archive_prevalidation_accepts_pax_long_name_extension() -> None:
    archive = _archive(format=tarfile.PAX_FORMAT, member_name="n" * 150)

    _validate_archive_member_ancestry(archive)


def test_archive_prevalidation_uses_pax_size_override_for_physical_scan() -> None:
    archive = _pax_size_override_archive()

    _validate_archive_member_ancestry(archive)
