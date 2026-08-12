from __future__ import annotations

import io
import tarfile

import pytest

import e2h.isolated_runner as isolated_runner
from e2h.isolated_runner import _validate_archive_member_ancestry
from e2h.runner import RunnerError
from e2h.workspace_archive import WorkspaceArchive


def _archive(
    *,
    member_pax: dict[str, str] | None = None,
    global_pax: dict[str, str] | None = None,
) -> WorkspaceArchive:
    stream = io.BytesIO()
    with tarfile.open(
        fileobj=stream,
        mode="w",
        format=tarfile.PAX_FORMAT,
        pax_headers=global_pax,
        encoding="utf-8",
        errors="surrogateescape",
    ) as handle:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        handle.addfile(root)

        payload = tarfile.TarInfo("payload.txt")
        payload.size = 0
        if member_pax:
            payload.pax_headers.update(member_pax)
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


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SCHILY.xattr.user.e2h", "blocked"),
        ("SCHILY.acl.access", "blocked"),
        ("LIBARCHIVE.xattr.user.e2h", "blocked"),
        ("GNU.sparse.map", "0,0"),
        ("VENDOR.unexpected", "blocked"),
    ],
)
def test_archive_ancestry_rejects_unsupported_member_pax_metadata(
    key: str,
    value: str,
) -> None:
    archive = _archive(member_pax={key: value})

    with pytest.raises(RunnerError, match="unsupported PAX metadata"):
        _validate_archive_member_ancestry(archive)


def test_unsupported_pax_key_is_rejected_before_logical_tar_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(member_pax={"GNU.sparse.map": "0,0"})

    def forbidden_open(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("unsupported PAX metadata must fail before tarfile.open")

    monkeypatch.setattr(isolated_runner.tarfile, "open", forbidden_open)

    with pytest.raises(RunnerError, match="unsupported PAX metadata"):
        _validate_archive_member_ancestry(archive)


def test_archive_ancestry_rejects_unsupported_global_pax_metadata() -> None:
    archive = _archive(global_pax={"SCHILY.xattr.user.e2h": "blocked"})

    with pytest.raises(RunnerError, match="unsupported global PAX metadata"):
        _validate_archive_member_ancestry(archive)


def test_archive_ancestry_rejects_global_pax_even_for_member_allowed_key() -> None:
    archive = _archive(global_pax={"mtime": "123.25"})

    with pytest.raises(RunnerError, match="unsupported global PAX metadata"):
        _validate_archive_member_ancestry(archive)
