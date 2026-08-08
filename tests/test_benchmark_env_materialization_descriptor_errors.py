from __future__ import annotations

import os
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


def test_descriptor_copy_rejects_file_destination_parent(tmp_path: Path) -> None:
    source = _source(tmp_path)
    parent = tmp_path / "not-a-directory"
    parent.write_text("file\n", encoding="utf-8")

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to prepare environment materialization destination",
    ):
        benchmark_env._copy_environment_tree(source, parent / "materialized")


def test_open_materialized_directory_rejects_file_component(tmp_path: Path) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    (root / "not-a-directory").write_text("file\n", encoding="utf-8")
    descriptor = _directory_descriptor(root)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="materialized environment path component is not a directory",
        ):
            benchmark_env._open_materialized_directory(
                descriptor,
                ("not-a-directory",),
                create=False,
            )
    finally:
        os.close(descriptor)


def test_remove_materialized_tree_removes_symlink_not_target(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "protected.txt"
    protected.write_text("keep\n", encoding="utf-8")
    link = parent / "materialized"
    link.symlink_to(outside, target_is_directory=True)
    descriptor = _directory_descriptor(parent)
    try:
        benchmark_env._remove_materialized_tree_at(descriptor, link.name)
    finally:
        os.close(descriptor)

    assert not link.exists()
    assert protected.read_text(encoding="utf-8") == "keep\n"


def test_scan_materialized_environment_rejects_list_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    descriptor = _directory_descriptor(root)
    original_listdir = os.listdir

    def failing_listdir(path: Any) -> list[str]:
        if path == descriptor:
            raise PermissionError("blocked list")
        return original_listdir(path)

    monkeypatch.setattr(os, "listdir", failing_listdir)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="unable to list materialized environment directory",
        ):
            benchmark_env._scan_materialized_environment(descriptor)
    finally:
        os.close(descriptor)


def test_scan_materialized_environment_rejects_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")
    descriptor = _directory_descriptor(root)
    original_stat_entry = benchmark_env._stat_materialization_entry

    def failing_stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
        if name == "value.txt":
            raise PermissionError("blocked stat")
        return original_stat_entry(parent_descriptor, name)

    monkeypatch.setattr(benchmark_env, "_stat_materialization_entry", failing_stat_entry)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="unable to stat materialized environment entry",
        ):
            benchmark_env._scan_materialized_environment(descriptor)
    finally:
        os.close(descriptor)


def test_descriptor_copy_cleans_destination_file_create_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == "value.txt" and kwargs.get("dir_fd") is not None and flags & os.O_CREAT:
            raise PermissionError("blocked destination file")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to create materialized environment file",
    ):
        benchmark_env._copy_environment_tree(source, destination)

    assert not destination.exists()


def test_descriptor_copy_cleans_source_file_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    victim = source / "value.txt"
    destination = tmp_path / "materialized"
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if Path(path) == victim and kwargs.get("dir_fd") is None:
            raise PermissionError("blocked source file")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to open environment file .* for materialization",
    ):
        benchmark_env._copy_environment_tree(source, destination)

    assert not destination.exists()


def test_descriptor_copy_cleans_source_file_replacement_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    victim = source / "value.txt"
    destination = tmp_path / "materialized"
    original_open = os.open
    swapped = False

    def replacing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path) == victim and kwargs.get("dir_fd") is None and not swapped:
            swapped = True
            victim.unlink()
            victim.write_text("replacement\n", encoding="utf-8")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replacing_open)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="environment file changed while opening",
    ):
        benchmark_env._copy_environment_tree(source, destination)

    assert swapped is True
    assert not destination.exists()
