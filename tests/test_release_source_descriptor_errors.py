from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.release_source as release_source
from e2h.release_source import ReleaseSourceError
from e2h.snapshot import SnapshotLimits


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)


def _raise_os_error(*args: Any, **kwargs: Any) -> Any:
    raise OSError("injected failure")


def test_resolve_source_root_normalizes_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ReleaseSourceError, match="unable to inspect release source root"):
        release_source._resolve_source_root(tmp_path / "missing")


def test_source_root_path_stability_normalizes_missing_root(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    root.rmdir()

    with pytest.raises(ReleaseSourceError, match="unable to restat release source root"):
        release_source._source_root_path_must_be_stable(root, expected)


def test_source_root_path_stability_rejects_replacement(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    root.rename(tmp_path / "original")
    root.mkdir()

    with pytest.raises(ReleaseSourceError, match="release source root changed during traversal"):
        release_source._source_root_path_must_be_stable(root, expected)


def test_source_root_descriptor_stability_normalizes_missing_path(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    descriptor = os.open(root, _directory_flags())
    opened = os.fstat(descriptor)
    root.rename(tmp_path / "moved")
    try:
        with pytest.raises(ReleaseSourceError, match="unable to restat release source root"):
            release_source._source_root_descriptor_must_be_stable(root, descriptor, opened)
    finally:
        os.close(descriptor)


def test_source_root_descriptor_stability_rejects_replacement(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    descriptor = os.open(root, _directory_flags())
    opened = os.fstat(descriptor)
    root.rename(tmp_path / "original")
    root.mkdir()
    try:
        with pytest.raises(ReleaseSourceError, match="release source root changed during traversal"):
            release_source._source_root_descriptor_must_be_stable(root, descriptor, opened)
    finally:
        os.close(descriptor)


def test_resolve_under_root_normalizes_missing_entry(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()

    with pytest.raises(ReleaseSourceError, match="unable to resolve source entry missing"):
        release_source._resolve_under_root(root, root / "missing", "missing")


def test_resolve_under_root_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("outside\n", encoding="utf-8")

    with pytest.raises(ReleaseSourceError, match="source path escapes root"):
        release_source._resolve_under_root(root, outside, "outside")


def test_list_source_directory_path_normalizes_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    monkeypatch.setattr(os, "open", _raise_os_error)

    with pytest.raises(ReleaseSourceError, match="unable to open source directory"):
        release_source._list_source_directory_path(root, root, ".", expected)


def test_list_source_directory_path_normalizes_list_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    monkeypatch.setattr(os, "listdir", _raise_os_error)

    with pytest.raises(ReleaseSourceError, match="unable to list source directory"):
        release_source._list_source_directory_path(root, root, ".", expected)


def test_list_source_directory_path_normalizes_restat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    moved = tmp_path / "moved"
    original_listdir = os.listdir

    def moving_listdir(path: Any) -> list[str]:
        names = original_listdir(path)
        root.rename(moved)
        return names

    monkeypatch.setattr(os, "listdir", moving_listdir)

    with pytest.raises(ReleaseSourceError, match="unable to restat source directory"):
        release_source._list_source_directory_path(root, root, ".", expected)


def test_hash_regular_file_path_rejects_expected_oversize(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "payload.bin"
    path.write_bytes(b"ab")
    expected = path.stat(follow_symlinks=False)

    with pytest.raises(ReleaseSourceError, match="source file exceeds max_file_bytes"):
        release_source._hash_regular_file_path(
            root,
            path,
            "payload.bin",
            expected,
            limits=SnapshotLimits(max_file_bytes=1),
        )


def test_hash_regular_file_path_normalizes_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "payload.bin"
    path.write_bytes(b"payload")
    expected = path.stat(follow_symlinks=False)
    monkeypatch.setattr(os, "open", _raise_os_error)

    with pytest.raises(ReleaseSourceError, match="unable to open source file"):
        release_source._hash_regular_file_path(
            root,
            path,
            "payload.bin",
            expected,
            limits=SnapshotLimits(),
        )


def test_hash_regular_file_path_rejects_non_regular_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "payload.bin"
    path.write_bytes(b"payload")
    expected = path.stat(follow_symlinks=False)
    directory_descriptor = os.open(root, _directory_flags())

    def open_directory(*args: Any, **kwargs: Any) -> int:
        return os.dup(directory_descriptor)

    monkeypatch.setattr(os, "open", open_directory)
    try:
        with pytest.raises(ReleaseSourceError, match="source entry is not a regular file"):
            release_source._hash_regular_file_path(
                root,
                path,
                "payload.bin",
                expected,
                limits=SnapshotLimits(),
            )
    finally:
        os.close(directory_descriptor)


def test_hash_regular_file_path_rejects_open_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    first = root / "first.bin"
    second = root / "second.bin"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    expected = first.stat(follow_symlinks=False)

    with pytest.raises(ReleaseSourceError, match="source file changed while opening"):
        release_source._hash_regular_file_path(
            root,
            second,
            "second.bin",
            expected,
            limits=SnapshotLimits(),
        )


def test_open_source_root_normalizes_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    monkeypatch.setattr(os, "open", _raise_os_error)

    with pytest.raises(ReleaseSourceError, match=r"unable to open source directory \.:"):
        release_source._open_source_root(root, expected)


def test_open_source_root_rejects_non_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    regular = tmp_path / "regular.txt"
    regular.write_text("file\n", encoding="utf-8")
    regular_descriptor = os.open(regular, os.O_RDONLY)

    def open_regular(*args: Any, **kwargs: Any) -> int:
        return os.dup(regular_descriptor)

    monkeypatch.setattr(os, "open", open_regular)
    try:
        with pytest.raises(ReleaseSourceError, match="source entry is no longer a directory"):
            release_source._open_source_root(root, expected)
    finally:
        os.close(regular_descriptor)


def test_open_source_root_rejects_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "source"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    expected = other.stat(follow_symlinks=False)

    with pytest.raises(ReleaseSourceError, match="source directory changed while opening"):
        release_source._open_source_root(root, expected)


def test_open_bound_source_directory_normalizes_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    child = root / "child"
    child.mkdir(parents=True)
    expected = child.stat(follow_symlinks=False)
    parent_descriptor = os.open(root, _directory_flags())
    monkeypatch.setattr(os, "open", _raise_os_error)
    try:
        with pytest.raises(ReleaseSourceError, match="unable to open source directory child"):
            release_source._open_bound_source_directory_at(
                parent_descriptor,
                "child",
                "child",
                expected,
            )
    finally:
        os.close(parent_descriptor)


def test_open_bound_source_directory_rejects_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "source"
    child = root / "child"
    other = root / "other"
    child.mkdir(parents=True)
    other.mkdir()
    expected = other.stat(follow_symlinks=False)
    parent_descriptor = os.open(root, _directory_flags())
    try:
        with pytest.raises(ReleaseSourceError, match="source directory changed while opening"):
            release_source._open_bound_source_directory_at(
                parent_descriptor,
                "child",
                "child",
                expected,
            )
    finally:
        os.close(parent_descriptor)


def test_list_source_directory_at_normalizes_list_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    descriptor = os.open(root, _directory_flags())
    opened = os.fstat(descriptor)
    monkeypatch.setattr(os, "listdir", _raise_os_error)
    try:
        with pytest.raises(ReleaseSourceError, match="unable to list source directory"):
            release_source._list_source_directory_at(descriptor, ".", opened)
    finally:
        os.close(descriptor)


def test_list_source_directory_at_rejects_descriptor_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "source"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    descriptor = os.open(root, _directory_flags())
    wrong_opened = other.stat(follow_symlinks=False)
    try:
        with pytest.raises(ReleaseSourceError, match="source directory changed while listing"):
            release_source._list_source_directory_at(descriptor, ".", wrong_opened)
    finally:
        os.close(descriptor)


def test_source_directory_frame_normalizes_fstat_failure(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    descriptor = os.open(root, _directory_flags())
    opened = os.fstat(descriptor)
    os.close(descriptor)
    frame = release_source._SourceDirectoryFrame(
        descriptor=descriptor,
        opened=opened,
        names=[],
        parts=(),
    )

    with pytest.raises(ReleaseSourceError, match="unable to restat source directory"):
        release_source._source_directory_frame_must_be_stable(frame)


def test_source_directory_frame_rejects_descriptor_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "source"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    descriptor = os.open(root, _directory_flags())
    wrong_opened = other.stat(follow_symlinks=False)
    frame = release_source._SourceDirectoryFrame(
        descriptor=descriptor,
        opened=wrong_opened,
        names=[],
        parts=(),
    )
    try:
        with pytest.raises(ReleaseSourceError, match="source directory changed while traversing"):
            release_source._source_directory_frame_must_be_stable(frame)
    finally:
        os.close(descriptor)


def test_hash_regular_file_at_rejects_expected_oversize(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "payload.bin"
    path.write_bytes(b"ab")
    expected = path.stat(follow_symlinks=False)
    parent_descriptor = os.open(root, _directory_flags())
    try:
        with pytest.raises(ReleaseSourceError, match="source file exceeds max_file_bytes"):
            release_source._hash_regular_file_at(
                parent_descriptor,
                path.name,
                path.name,
                expected,
                limits=SnapshotLimits(max_file_bytes=1),
            )
    finally:
        os.close(parent_descriptor)


def test_hash_regular_file_at_normalizes_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "payload.bin"
    path.write_bytes(b"payload")
    expected = path.stat(follow_symlinks=False)
    parent_descriptor = os.open(root, _directory_flags())
    monkeypatch.setattr(os, "open", _raise_os_error)
    try:
        with pytest.raises(ReleaseSourceError, match="unable to open source file"):
            release_source._hash_regular_file_at(
                parent_descriptor,
                path.name,
                path.name,
                expected,
                limits=SnapshotLimits(),
            )
    finally:
        os.close(parent_descriptor)


def test_hash_regular_file_at_rejects_non_regular_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = root / "payload.bin"
    path.write_bytes(b"payload")
    expected = path.stat(follow_symlinks=False)
    parent_descriptor = os.open(root, _directory_flags())

    def open_parent(*args: Any, **kwargs: Any) -> int:
        return os.dup(parent_descriptor)

    monkeypatch.setattr(os, "open", open_parent)
    try:
        with pytest.raises(ReleaseSourceError, match="source entry is not a regular file"):
            release_source._hash_regular_file_at(
                parent_descriptor,
                path.name,
                path.name,
                expected,
                limits=SnapshotLimits(),
            )
    finally:
        os.close(parent_descriptor)


def test_hash_regular_file_at_rejects_open_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    first = root / "first.bin"
    second = root / "second.bin"
    first.write_bytes(b"same")
    second.write_bytes(b"same")
    expected = first.stat(follow_symlinks=False)
    parent_descriptor = os.open(root, _directory_flags())
    try:
        with pytest.raises(ReleaseSourceError, match="source file changed while opening"):
            release_source._hash_regular_file_at(
                parent_descriptor,
                second.name,
                second.name,
                expected,
                limits=SnapshotLimits(),
            )
    finally:
        os.close(parent_descriptor)
