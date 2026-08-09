from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.atomic_write as atomic_write


def _open_directory(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    return descriptor, os.fstat(descriptor)


def test_atomic_parent_preparation_and_restat_errors(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("file\n", encoding="utf-8")
    with pytest.raises(OSError, match="prepare atomic output parent"):
        atomic_write._prepare_parent(blocker / "result.json")

    missing = tmp_path / "missing"
    with pytest.raises(OSError, match="restat atomic output parent"):
        atomic_write._requested_parent_identity(missing)


def test_atomic_parent_path_stability_rejects_replacement(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    requested, _, expected = atomic_write._prepare_parent(parent / "result.json")
    moved = tmp_path / "moved"
    parent.rename(moved)
    parent.mkdir()

    with pytest.raises(OSError, match="parent changed while writing"):
        atomic_write._parent_path_must_be_stable(requested, expected)


def test_atomic_parent_descriptor_restat_wraps_closed_descriptor(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    requested, resolved, _ = atomic_write._prepare_parent(parent / "result.json")
    descriptor, opened = _open_directory(resolved)
    os.close(descriptor)

    with pytest.raises(OSError, match="restat atomic output parent"):
        atomic_write._parent_descriptor_must_be_stable(requested, descriptor, opened)


def test_atomic_temporary_identity_rejects_non_regular_descriptor(tmp_path: Path) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()
    descriptor, _ = _open_directory(directory)
    try:
        with pytest.raises(OSError, match="temporary output is not a regular file"):
            atomic_write._temporary_identity(descriptor)
    finally:
        os.close(descriptor)


def test_atomic_payload_detects_descriptor_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")
    descriptor = os.open(first, os.O_WRONLY)
    original_fstat = os.fstat
    calls = 0

    def swapping_fstat(fd: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls >= 2:
            return second.stat(follow_symlinks=False)
        return original_fstat(fd)

    monkeypatch.setattr(atomic_write.os, "fstat", swapping_fstat)
    try:
        with pytest.raises(OSError, match="temporary output changed while writing"):
            atomic_write._write_payload(descriptor, "value")
    finally:
        os.close(descriptor)


def test_atomic_identity_cleanup_tolerates_bad_parent_and_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "target"
    target.write_text("target\n", encoding="utf-8")
    target_info = target.stat(follow_symlinks=False)
    descriptor, _ = _open_directory(parent)
    os.close(descriptor)
    atomic_write._remove_regular_file_by_identity_at(
        descriptor,
        (target_info.st_dev, target_info.st_ino),
    )
    assert target.is_file()

    descriptor, _ = _open_directory(parent)
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == target.name and kwargs.get("dir_fd") == descriptor:
            raise OSError("injected open failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(atomic_write.os, "open", failing_open)
    try:
        atomic_write._remove_regular_file_by_identity_at(
            descriptor,
            (target_info.st_dev, target_info.st_ino),
        )
    finally:
        os.close(descriptor)
    assert target.is_file()


def test_atomic_identity_cleanup_removes_matching_regular_file(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "target"
    target.write_text("target\n", encoding="utf-8")
    other = parent / "other"
    other.write_text("other\n", encoding="utf-8")
    target_info = target.stat(follow_symlinks=False)
    descriptor, _ = _open_directory(parent)
    try:
        atomic_write._remove_regular_file_by_identity_at(
            descriptor,
            (target_info.st_dev, target_info.st_ino),
        )
    finally:
        os.close(descriptor)

    assert not target.exists()
    assert other.read_text(encoding="utf-8") == "other\n"


@pytest.mark.skipif(
    not atomic_write._ATOMIC_DIR_FD_SUPPORTED,
    reason="descriptor-relative atomic publication is unavailable",
)
def test_atomic_temporary_name_exhaustion_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    descriptor, _ = _open_directory(parent)
    name = ".result.json.fixed.tmp"
    (parent / name).write_text("occupied\n", encoding="utf-8")
    monkeypatch.setattr(atomic_write.secrets, "token_hex", lambda length: "fixed")
    try:
        with pytest.raises(OSError, match="allocate a unique atomic temporary output"):
            atomic_write._create_temporary_at(descriptor, "result.json")
    finally:
        os.close(descriptor)


def test_atomic_path_unlink_handles_missing_mismatch_and_match(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    atomic_write._unlink_path_if_identity(missing, (1, 1))

    target = tmp_path / "target"
    target.write_text("target\n", encoding="utf-8")
    info = target.stat(follow_symlinks=False)
    atomic_write._unlink_path_if_identity(target, (0, 0))
    assert target.is_file()

    atomic_write._unlink_path_if_identity(target, (info.st_dev, info.st_ino))
    assert not target.exists()


def test_atomic_descriptor_parent_open_and_identity_errors(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    requested, resolved, expected = atomic_write._prepare_parent(parent / "result.json")
    missing = tmp_path / "missing"
    with pytest.raises(OSError, match="open atomic output parent"):
        atomic_write._write_text_atomic_descriptor(
            requested,
            missing,
            expected,
            "result.json",
            "value",
        )

    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(OSError, match="parent changed while opening"):
        atomic_write._write_text_atomic_descriptor(
            requested,
            resolved,
            other.stat(follow_symlinks=False),
            "result.json",
            "value",
        )


def test_atomic_path_fallback_rejects_temporary_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "result.json"
    requested, parent, expected = atomic_write._prepare_parent(output)
    original_write = atomic_write._write_payload
    moved = tmp_path / "written-original.tmp"
    substitute: Path | None = None

    def substituting_write(descriptor: int, payload: str) -> None:
        nonlocal substitute
        temporary = next(tmp_path.glob(".result.json.*.tmp"))
        temporary.rename(moved)
        temporary.write_text("attacker\n", encoding="utf-8")
        substitute = temporary
        original_write(descriptor, payload)

    monkeypatch.setattr(atomic_write, "_write_payload", substituting_write)

    with pytest.raises(OSError, match="temporary output changed before publication"):
        atomic_write._write_text_atomic_path(
            requested,
            parent,
            expected,
            output.name,
            "safe\n",
        )

    assert substitute is not None
    assert substitute.read_text(encoding="utf-8") == "attacker\n"
    assert moved.is_file()
    assert not output.exists()
