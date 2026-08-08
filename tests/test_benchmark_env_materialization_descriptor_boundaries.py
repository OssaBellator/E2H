from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

import e2h.benchmark_env as benchmark_env
from e2h.benchmark_env import BenchmarkEnvironmentError

pytestmark = pytest.mark.skipif(
    not benchmark_env._MATERIALIZATION_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative benchmark materialization support",
)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "value.txt").write_text("inside\n", encoding="utf-8")
    return source


def _directory_descriptor(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def test_descriptor_copy_rejects_existing_destination(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    destination.mkdir()

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="environment materialization destination already exists",
    ):
        benchmark_env._copy_environment_tree(source, destination)


def test_descriptor_copy_rejects_non_directory_source(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("not a directory\n", encoding="utf-8")
    destination = tmp_path / "materialized"

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="environment source_dir is not a directory",
    ):
        benchmark_env._copy_environment_tree(source, destination)

    assert not destination.exists()


def test_descriptor_copy_rejects_destination_parent_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_parent = tmp_path / "source-parent"
    source_parent.mkdir()
    source = source_parent / "source"
    source.mkdir()
    (source / "value.txt").write_text("inside\n", encoding="utf-8")
    destination_parent = tmp_path / "destination-parent"
    destination_parent.mkdir()
    destination = destination_parent / "materialized"
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if Path(path) == destination_parent and kwargs.get("dir_fd") is None:
            raise PermissionError("blocked parent open")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to open environment materialization destination parent",
    ):
        benchmark_env._copy_environment_tree(source, destination)


def test_descriptor_copy_rejects_destination_creation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    original_mkdir = os.mkdir

    def failing_mkdir(
        path: Any,
        mode: int = 0o777,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if Path(path).name == destination.name and kwargs.get("dir_fd") is not None:
            raise PermissionError("blocked destination mkdir")
        original_mkdir(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", failing_mkdir)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to create benchmark environment destination",
    ):
        benchmark_env._copy_environment_tree(source, destination)

    assert not destination.exists()


def test_open_materialized_directory_rejects_missing_without_create(tmp_path: Path) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    descriptor = _directory_descriptor(root)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="materialized environment directory is missing",
        ):
            benchmark_env._open_materialized_directory(
                descriptor,
                ("missing",),
                create=False,
            )
    finally:
        os.close(descriptor)


def test_remove_materialized_tree_removes_non_directory_entry(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    loose = parent / "loose.txt"
    loose.write_text("remove me\n", encoding="utf-8")
    descriptor = _directory_descriptor(parent)
    try:
        benchmark_env._remove_materialized_tree_at(descriptor, loose.name)
    finally:
        os.close(descriptor)

    assert not loose.exists()


def test_scan_materialized_environment_rejects_symlink(tmp_path: Path) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside\n", encoding="utf-8")
    (root / "value.txt").symlink_to(target)
    descriptor = _directory_descriptor(root)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="materialized environment contains symlink",
        ):
            benchmark_env._scan_materialized_environment(descriptor)
    finally:
        os.close(descriptor)


def test_scan_materialized_environment_rejects_non_regular_entry(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("mkfifo is unavailable")
    root = tmp_path / "materialized"
    root.mkdir()
    os.mkfifo(root / "pipe")
    descriptor = _directory_descriptor(root)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="materialized environment contains non-regular file",
        ):
            benchmark_env._scan_materialized_environment(descriptor)
    finally:
        os.close(descriptor)


def test_hash_materialized_file_rejects_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    value = root / "value.txt"
    value.write_text("inside\n", encoding="utf-8")
    descriptor = _directory_descriptor(root)
    expected = value.stat(follow_symlinks=False)
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == value.name and kwargs.get("dir_fd") == descriptor:
            raise PermissionError("blocked file open")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="unable to open materialized environment file",
        ):
            benchmark_env._hash_materialized_file(
                descriptor,
                value.name,
                value.name,
                expected,
            )
    finally:
        os.close(descriptor)


def test_descriptor_copy_cleans_file_metadata_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    original_fchmod = os.fchmod
    failed = False

    def failing_fchmod(descriptor: int, mode: int) -> None:
        nonlocal failed
        if not failed and stat.S_ISREG(os.fstat(descriptor).st_mode):
            failed = True
            raise PermissionError("blocked file metadata")
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(os, "fchmod", failing_fchmod)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to copy environment file",
    ):
        benchmark_env._copy_environment_tree(source, destination)

    assert failed is True
    assert not destination.exists()


def test_descriptor_copy_cleans_directory_metadata_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    original_fchmod = os.fchmod
    failed = False

    def failing_fchmod(descriptor: int, mode: int) -> None:
        nonlocal failed
        if not failed and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            failed = True
            raise PermissionError("blocked directory metadata")
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(os, "fchmod", failing_fchmod)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to preserve materialized environment directory metadata",
    ):
        benchmark_env._copy_environment_tree(source, destination)

    assert failed is True
    assert not destination.exists()
