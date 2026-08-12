from __future__ import annotations

import io
import tarfile

import pytest

from e2h.isolated_runner import _validate_archive_member_ancestry
from e2h.runner import RunnerError
from e2h.workspace_archive import WorkspaceArchive


def _archive(members: list[tarfile.TarInfo]) -> WorkspaceArchive:
    stream = io.BytesIO()
    with tarfile.open(
        fileobj=stream,
        mode="w",
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="surrogateescape",
    ) as handle:
        for member in members:
            handle.addfile(member)
    archive_bytes = stream.tell()
    stream.seek(0)
    directories = frozenset(member.name for member in members if member.isdir())
    return WorkspaceArchive(
        file=stream,
        directories=directories,
        source_bytes=0,
        entries=max(0, len(members) - 1),
        archive_bytes=archive_bytes,
    )


def _root() -> tarfile.TarInfo:
    member = tarfile.TarInfo(".")
    member.type = tarfile.DIRTYPE
    return member


def test_archive_ancestry_rejects_non_utf8_member_path() -> None:
    bad = tarfile.TarInfo("bad_\udcff")
    bad.size = 0
    archive = _archive([_root(), bad])

    with pytest.raises(RunnerError, match="member path is not valid UTF-8"):
        _validate_archive_member_ancestry(archive)


def test_archive_ancestry_rejects_non_utf8_symlink_target() -> None:
    link = tarfile.TarInfo("link")
    link.type = tarfile.SYMTYPE
    link.linkname = "target_\udcff"
    archive = _archive([_root(), link])

    with pytest.raises(RunnerError, match="symlink target is not valid UTF-8"):
        _validate_archive_member_ancestry(archive)


def test_archive_ancestry_allows_valid_multilingual_utf8_paths() -> None:
    directory = tarfile.TarInfo("café")
    directory.type = tarfile.DIRTYPE
    payload = tarfile.TarInfo("café/文件.txt")
    payload.size = 0
    archive = _archive([_root(), directory, payload])

    _validate_archive_member_ancestry(archive)
