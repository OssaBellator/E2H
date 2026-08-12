from __future__ import annotations

import io
import tarfile

import pytest

from e2h.isolated_runner import _validate_archive_member_ancestry
from e2h.runner import RunnerError
from e2h.workspace_archive import WorkspaceArchive


def test_raw_archive_prevalidation_rejects_alternate_regular_file_type() -> None:
    root = tarfile.TarInfo(".")
    root.type = tarfile.DIRTYPE
    root_header = root.tobuf(
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="surrogateescape",
    )

    payload = tarfile.TarInfo("payload.txt")
    payload.type = tarfile.AREGTYPE
    payload.size = 0
    payload_header = payload.tobuf(
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="surrogateescape",
    )

    trailer = b"\0" * (2 * tarfile.BLOCKSIZE)
    stream = io.BytesIO(root_header + payload_header + trailer)
    archive = WorkspaceArchive(
        file=stream,
        directories=frozenset({"."}),
        source_bytes=0,
        entries=1,
        archive_bytes=len(stream.getbuffer()),
    )

    with pytest.raises(RunnerError, match="unsupported tar header type"):
        _validate_archive_member_ancestry(archive)
