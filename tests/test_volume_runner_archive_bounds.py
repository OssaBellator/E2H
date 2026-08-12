from __future__ import annotations

import io
import tarfile

import pytest

from e2h.runner import RunnerError
from e2h.volume_runner import _workspace_tree
from e2h.workspace_archive import (
    _MAX_ARCHIVE_DEPTH,
    _MAX_ARCHIVE_MEMBER_PATH_BYTES,
    WorkspaceArchive,
)


def _archive_from_stream(
    stream: io.BytesIO,
    *,
    directories: frozenset[str] = frozenset({"."}),
    source_bytes: int = 0,
    entries: int = 0,
    archive_bytes: int | None = None,
) -> WorkspaceArchive:
    physical_bytes = len(stream.getbuffer())
    stream.seek(0)
    return WorkspaceArchive(
        file=stream,
        directories=directories,
        source_bytes=source_bytes,
        entries=entries,
        archive_bytes=physical_bytes if archive_bytes is None else archive_bytes,
    )


def _root_tar(*, mode: str = "w") -> io.BytesIO:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode=mode, format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
    return stream


def test_workspace_tree_accepts_capture_format_zero_trailer() -> None:
    tree = _workspace_tree(_archive_from_stream(_root_tar()))

    assert tree.directories == frozenset({"."})
    assert tree.symlinks == ()


def test_workspace_tree_rejects_compressed_archive() -> None:
    archive = _archive_from_stream(_root_tar(mode="w:gz"))

    with pytest.raises(RunnerError, match="unable to inspect sealed workspace archive"):
        _workspace_tree(archive)


def test_workspace_tree_rejects_physical_size_mismatch() -> None:
    stream = _root_tar()
    archive = _archive_from_stream(stream, archive_bytes=len(stream.getbuffer()) + 1)

    with pytest.raises(RunnerError, match="size does not match captured metadata"):
        _workspace_tree(archive)


def test_workspace_tree_rejects_concatenated_tar_after_end_marker() -> None:
    stream = io.BytesIO(_root_tar().getvalue() + _root_tar().getvalue())
    archive = _archive_from_stream(stream)

    with pytest.raises(RunnerError, match="invalid trailer padding"):
        _workspace_tree(archive)


def test_workspace_tree_rejects_nonzero_trailing_payload() -> None:
    payload = bytearray(_root_tar().getvalue())
    payload[-1] = 1
    archive = _archive_from_stream(io.BytesIO(payload))

    with pytest.raises(RunnerError, match="non-zero trailing data"):
        _workspace_tree(archive)


def test_workspace_tree_rejects_member_count_overrun() -> None:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        extra = tarfile.TarInfo("extra")
        extra.type = tarfile.DIRTYPE
        handle.addfile(extra)
    archive = _archive_from_stream(stream, entries=0)

    with pytest.raises(RunnerError, match="capture metadata does not match archive bytes"):
        _workspace_tree(archive)


def test_workspace_tree_rejects_source_byte_overrun() -> None:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        member = tarfile.TarInfo("data.bin")
        member.size = 1
        handle.addfile(member, io.BytesIO(b"x"))
    archive = _archive_from_stream(stream, entries=1, source_bytes=0)

    with pytest.raises(RunnerError, match="capture metadata does not match archive bytes"):
        _workspace_tree(archive)


def test_workspace_tree_rejects_member_beyond_capture_depth() -> None:
    name = "/".join("d" for _ in range(_MAX_ARCHIVE_DEPTH + 1))
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        member = tarfile.TarInfo(name)
        member.type = tarfile.DIRTYPE
        handle.addfile(member)
    archive = _archive_from_stream(stream, entries=1)

    with pytest.raises(RunnerError, match="exceeds capture depth"):
        _workspace_tree(archive)


def test_workspace_tree_rejects_member_beyond_capture_path_bound() -> None:
    name = "x" * (_MAX_ARCHIVE_MEMBER_PATH_BYTES + 1)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)
        member = tarfile.TarInfo(name)
        member.type = tarfile.DIRTYPE
        handle.addfile(member)
    archive = _archive_from_stream(stream, entries=1)

    with pytest.raises(RunnerError, match="exceeds capture path bound"):
        _workspace_tree(archive)
