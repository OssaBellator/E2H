from __future__ import annotations

import io
import tarfile
from collections.abc import Callable

import pytest

from e2h.runner import RunnerError
from e2h.volume_runner import _workspace_tree
from e2h.workspace_archive import WorkspaceArchive


def _archive(
    configure: Callable[[tarfile.TarInfo], None],
    *,
    member_type: bytes = tarfile.REGTYPE,
    payload: bytes = b"",
    link_target: str = ".",
) -> WorkspaceArchive:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)

        member = tarfile.TarInfo("entry")
        member.type = member_type
        if member_type == tarfile.SYMTYPE:
            member.linkname = link_target
        member.size = len(payload)
        configure(member)
        handle.addfile(member, io.BytesIO(payload) if payload else None)

    archive_bytes = stream.tell()
    stream.seek(0)
    directories = {"."}
    if member_type == tarfile.DIRTYPE:
        directories.add("entry")
    source_bytes = len(link_target.encode("utf-8")) if member_type == tarfile.SYMTYPE else len(payload)
    if member_type == tarfile.DIRTYPE:
        source_bytes = 0
    return WorkspaceArchive(
        file=stream,
        directories=frozenset(directories),
        source_bytes=source_bytes,
        entries=1,
        archive_bytes=archive_bytes,
    )


@pytest.mark.parametrize("field", ["uname", "gname"])
def test_workspace_tree_rejects_archive_owner_names(field: str) -> None:
    def configure(member: tarfile.TarInfo) -> None:
        setattr(member, field, "forged-owner")

    with pytest.raises(RunnerError, match="producer-incompatible metadata"):
        _workspace_tree(_archive(configure))


@pytest.mark.parametrize("member_type", [tarfile.DIRTYPE, tarfile.SYMTYPE])
def test_workspace_tree_rejects_payload_size_on_nonfile_member(member_type: bytes) -> None:
    with pytest.raises(RunnerError, match="producer-incompatible metadata"):
        _workspace_tree(_archive(lambda member: None, member_type=member_type, payload=b"abc"))


@pytest.mark.parametrize("member_type", [tarfile.REGTYPE, tarfile.DIRTYPE])
def test_workspace_tree_rejects_link_target_on_non_symlink(member_type: bytes) -> None:
    def configure(member: tarfile.TarInfo) -> None:
        member.linkname = "ignored-target"

    with pytest.raises(RunnerError, match="producer-incompatible metadata"):
        _workspace_tree(_archive(configure, member_type=member_type))


def test_workspace_tree_rejects_mode_bits_outside_stat_imode() -> None:
    def configure(member: tarfile.TarInfo) -> None:
        member.mode = 0o10000

    with pytest.raises(RunnerError, match="producer-incompatible metadata"):
        _workspace_tree(_archive(configure))
