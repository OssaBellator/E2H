from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.snapshot_source as source
from e2h.snapshot import SnapshotError, SnapshotLimits

pytestmark = pytest.mark.skipif(
    not source._SOURCE_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative snapshot source traversal",
)

_LIMITS = SnapshotLimits(
    max_entries=100,
    max_file_bytes=10_000,
    max_total_bytes=10_000,
)


def _open_directory(path: Path) -> tuple[int, os.stat_result]:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    return descriptor, os.fstat(descriptor)


def test_snapshot_source_root_boundary_errors(tmp_path: Path) -> None:
    with pytest.raises(SnapshotError, match="inspect snapshot root"):
        source.resolve_snapshot_source_root(tmp_path / "missing")

    file_root = tmp_path / "file"
    file_root.write_text("value\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="not a directory"):
        source.resolve_snapshot_source_root(file_root)

    root = tmp_path / "root"
    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    root.rmdir()
    with pytest.raises(SnapshotError, match="restat snapshot root"):
        source._root_path_must_be_stable(root, expected)

    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    moved = tmp_path / "moved"
    root.rename(moved)
    root.mkdir()
    with pytest.raises(SnapshotError, match="root changed"):
        source._root_path_must_be_stable(root, expected)

    root.rmdir()
    moved.rename(root)
    expected = root.stat(follow_symlinks=False)
    root.rmdir()
    with pytest.raises(SnapshotError, match="restat snapshot root"):
        source._root_path_contents_must_be_stable(root, expected)


def test_snapshot_source_rejects_invalid_include_and_root_open_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SnapshotError, match="safe relative path"):
        source._include_parts("../escape")

    root = tmp_path / "root"
    root.mkdir()
    expected = root.stat(follow_symlinks=False)
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if Path(path) == root:
            raise OSError("injected root open failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(source.os, "open", failing_open)
    with pytest.raises(SnapshotError, match="unable to open snapshot root"):
        source._open_root(root, expected)

    monkeypatch.setattr(source.os, "open", original_open)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(SnapshotError, match="changed while opening"):
        source._open_root(root, other.stat(follow_symlinks=False))


def test_snapshot_source_directory_open_list_and_frame_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    child = root / "child"
    child.mkdir()
    other = root / "other"
    other.mkdir()
    parent_descriptor, _ = _open_directory(root)
    child_descriptor: int | None = None
    try:
        with pytest.raises(SnapshotError, match="changed while opening"):
            source._open_directory_at(
                parent_descriptor,
                child.name,
                "child",
                other.stat(follow_symlinks=False),
            )

        child_descriptor, child_opened = source._open_directory_at(
            parent_descriptor,
            child.name,
            "child",
            child.stat(follow_symlinks=False),
        )
        original_listdir = os.listdir

        def failing_listdir(descriptor: int) -> list[str]:
            raise OSError(f"injected list failure for {descriptor}")

        monkeypatch.setattr(source.os, "listdir", failing_listdir)
        with pytest.raises(SnapshotError, match="unable to list"):
            source._list_directory_at(child_descriptor, "child", child_opened)

        monkeypatch.setattr(source.os, "listdir", original_listdir)

        def mutating_listdir(descriptor: int) -> list[str]:
            names = original_listdir(descriptor)
            (child / "late.txt").write_text("late\n", encoding="utf-8")
            return names

        monkeypatch.setattr(source.os, "listdir", mutating_listdir)
        with pytest.raises(SnapshotError, match="changed while listing"):
            source._list_directory_at(child_descriptor, "child", child_opened)

        monkeypatch.setattr(source.os, "listdir", original_listdir)
        frame = source._SourceDirectoryFrame(
            descriptor=child_descriptor,
            opened=child_opened,
            names=[],
            parts=("child",),
            parent_descriptor=parent_descriptor,
            name=child.name,
        )
        (child / "more.txt").write_text("more\n", encoding="utf-8")
        with pytest.raises(SnapshotError, match="changed while traversing"):
            source._frame_must_be_stable(frame)

        os.close(child_descriptor)
        child_descriptor = None
        with pytest.raises(SnapshotError, match="unable to restat directory"):
            source._frame_must_be_stable(frame)
    finally:
        if child_descriptor is not None:
            os.close(child_descriptor)
        os.close(parent_descriptor)


def test_snapshot_source_file_read_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    small = root / "small.txt"
    small.write_bytes(b"abc")
    other = root / "other.txt"
    other.write_bytes(b"xyz")
    directory = root / "directory"
    directory.mkdir()
    parent_descriptor, _ = _open_directory(root)
    original_fstat = os.fstat
    try:
        with pytest.raises(SnapshotError, match="max_file_bytes"):
            source._read_file_at(
                parent_descriptor,
                small.name,
                small.name,
                small.stat(follow_symlinks=False),
                limits=SnapshotLimits(
                    max_entries=10,
                    max_file_bytes=2,
                    max_total_bytes=10,
                ),
            )

        with pytest.raises(SnapshotError, match="no longer a regular file"):
            source._read_file_at(
                parent_descriptor,
                directory.name,
                directory.name,
                directory.stat(follow_symlinks=False),
                limits=_LIMITS,
            )

        with pytest.raises(SnapshotError, match="changed while opening"):
            source._read_file_at(
                parent_descriptor,
                small.name,
                small.name,
                other.stat(follow_symlinks=False),
                limits=_LIMITS,
            )

        original_fdopen = os.fdopen

        def growing_fdopen(descriptor: int, *args: Any, **kwargs: Any) -> Any:
            with small.open("ab") as handle:
                handle.write(b"0123456789")
            return original_fdopen(descriptor, *args, **kwargs)

        monkeypatch.setattr(source.os, "fdopen", growing_fdopen)
        with pytest.raises(SnapshotError, match="max_file_bytes"):
            source._read_file_at(
                parent_descriptor,
                small.name,
                small.name,
                small.stat(follow_symlinks=False),
                limits=SnapshotLimits(
                    max_entries=10,
                    max_file_bytes=5,
                    max_total_bytes=100,
                ),
            )
        monkeypatch.setattr(source.os, "fdopen", original_fdopen)

        expected = small.stat(follow_symlinks=False)
        calls = 0

        def failing_fstat(descriptor: int) -> os.stat_result:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected post-read stat failure")
            return original_fstat(descriptor)

        monkeypatch.setattr(source.os, "fstat", failing_fstat)
        with pytest.raises(SnapshotError, match="unable to read"):
            source._read_file_at(
                parent_descriptor,
                small.name,
                small.name,
                expected,
                limits=_LIMITS,
            )
    finally:
        monkeypatch.setattr(source.os, "fstat", original_fstat)
        os.close(parent_descriptor)


def test_snapshot_source_nested_include_guards_and_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "root"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_text("value\n", encoding="utf-8")
    root_info = root.stat(follow_symlinks=False)

    entries, _, _ = source._collect_descriptor(
        root,
        root_info,
        includes=("a/b/file.txt",),
        patterns=(),
        ignored=set(),
        limits=_LIMITS,
    )
    assert [entry.path for entry in entries] == ["a/b/file.txt"]

    root_descriptor, _ = source._open_root(root, root_info)
    try:
        with pytest.raises(SnapshotError, match="include path does not exist"):
            source._open_guard_directories(root_descriptor, ("missing",))

        blocker = root / "blocker"
        blocker.write_text("blocker\n", encoding="utf-8")
        with pytest.raises(SnapshotError, match="parent must be a directory"):
            source._open_guard_directories(root_descriptor, (blocker.name,))

        first = root / "first"
        first.mkdir()
        bad = first / "bad"
        bad.write_text("bad\n", encoding="utf-8")
        with pytest.raises(SnapshotError, match="parent must be a directory"):
            source._open_guard_directories(root_descriptor, (first.name, bad.name))
    finally:
        os.close(root_descriptor)


def test_snapshot_source_add_helpers_and_frame_errors(tmp_path: Path) -> None:
    selected = {}
    blobs = {}
    total = source._add_file(
        selected,
        blobs,
        "a.txt",
        b"a",
        False,
        limits=_LIMITS,
        total_bytes=0,
    )
    assert (
        source._add_file(
            selected,
            blobs,
            "a.txt",
            b"other",
            False,
            limits=_LIMITS,
            total_bytes=total,
        )
        == total
    )

    with pytest.raises(SnapshotError, match="max_total_bytes"):
        source._add_file(
            {},
            {},
            "large.txt",
            b"xx",
            False,
            limits=SnapshotLimits(
                max_entries=2,
                max_file_bytes=10,
                max_total_bytes=1,
            ),
            total_bytes=0,
        )

    assert source._add_directory(selected, "directory", limits=_LIMITS)
    assert source._add_directory(selected, "directory", limits=_LIMITS) is False

    root = tmp_path / "root"
    root.mkdir()
    descriptor, opened = _open_directory(root)
    try:
        frame = source._SourceDirectoryFrame(
            descriptor=descriptor,
            opened=opened,
            names=["missing"],
            parts=(),
        )
        with pytest.raises(SnapshotError, match="unable to stat"):
            source._process_descriptor_frame(
                [frame],
                {},
                {},
                set(),
                (),
                _LIMITS,
                0,
            )

        link = root / "link"
        link.symlink_to(tmp_path / "outside")
        frame = source._SourceDirectoryFrame(
            descriptor=descriptor,
            opened=root.stat(follow_symlinks=False),
            names=[link.name],
            parts=(),
        )
        with pytest.raises(SnapshotError, match="symbolic links"):
            source._process_descriptor_frame(
                [frame],
                {},
                {},
                set(),
                (),
                _LIMITS,
                0,
            )

        if hasattr(os, "mkfifo"):
            fifo = root / "fifo"
            os.mkfifo(fifo)
            frame = source._SourceDirectoryFrame(
                descriptor=descriptor,
                opened=root.stat(follow_symlinks=False),
                names=[fifo.name],
                parts=(),
            )
            with pytest.raises(SnapshotError, match="unsupported filesystem entry"):
                source._process_descriptor_frame(
                    [frame],
                    {},
                    {},
                    set(),
                    (),
                    _LIMITS,
                    0,
                )
    finally:
        os.close(descriptor)


def test_snapshot_source_explicit_include_errors_and_duplicates(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    directory = root / "directory"
    directory.mkdir()
    (directory / "value.txt").write_text("value\n", encoding="utf-8")
    file_entry = root / "file.txt"
    file_entry.write_text("file\n", encoding="utf-8")
    link = root / "link"
    link.symlink_to(file_entry)
    root_info = root.stat(follow_symlinks=False)

    with pytest.raises(SnapshotError, match="include path does not exist"):
        source._collect_descriptor(
            root,
            root_info,
            includes=("missing",),
            patterns=(),
            ignored=set(),
            limits=_LIMITS,
        )

    entries, _, _ = source._collect_descriptor(
        root,
        root.stat(follow_symlinks=False),
        includes=(file_entry.name,),
        patterns=(file_entry.name,),
        ignored=set(),
        limits=_LIMITS,
    )
    assert entries == []

    with pytest.raises(SnapshotError, match="symbolic links"):
        source._collect_descriptor(
            root,
            root.stat(follow_symlinks=False),
            includes=(link.name,),
            patterns=(),
            ignored=set(),
            limits=_LIMITS,
        )

    entries, _, _ = source._collect_descriptor(
        root,
        root.stat(follow_symlinks=False),
        includes=(directory.name, directory.name),
        patterns=(),
        ignored=set(),
        limits=_LIMITS,
    )
    assert [entry.path for entry in entries] == [
        "directory",
        "directory/value.txt",
    ]

    if hasattr(os, "mkfifo"):
        fifo = root / "fifo"
        os.mkfifo(fifo)
        with pytest.raises(SnapshotError, match="unsupported filesystem entry"):
            source._collect_descriptor(
                root,
                root.stat(follow_symlinks=False),
                includes=(fifo.name,),
                patterns=(),
                ignored=set(),
                limits=_LIMITS,
            )


def test_snapshot_source_path_fallback_resource_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_bytes(b"aa")
    (root / "b.txt").write_bytes(b"bb")
    root_info = root.stat(follow_symlinks=False)
    monkeypatch.setattr(source, "_SOURCE_DIR_FD_SUPPORTED", False)

    with pytest.raises(SnapshotError, match="max_entries"):
        source.collect_snapshot_source(
            root,
            root_info,
            includes=(".",),
            excludes=(),
            ignored_paths=set(),
            limits=SnapshotLimits(
                max_entries=1,
                max_file_bytes=10,
                max_total_bytes=10,
            ),
        )

    with pytest.raises(SnapshotError, match="max_total_bytes"):
        source.collect_snapshot_source(
            root,
            root.stat(follow_symlinks=False),
            includes=("a.txt",),
            excludes=(),
            ignored_paths=set(),
            limits=SnapshotLimits(
                max_entries=10,
                max_file_bytes=10,
                max_total_bytes=1,
            ),
        )
