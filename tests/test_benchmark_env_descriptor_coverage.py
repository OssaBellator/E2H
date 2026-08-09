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


def test_open_materialization_parent_rejects_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "parent" / "materialized"
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if Path(path) == destination.parent:
            raise PermissionError("blocked parent open")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)
    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to open environment materialization destination parent",
    ):
        benchmark_env._open_materialization_parent(destination)


def test_open_materialization_parent_rejects_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "parent" / "materialized"
    original_identity = benchmark_env._stat_identity
    calls = 0

    def mismatched_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
        nonlocal calls
        calls += 1
        identity = original_identity(info)
        if calls == 2:
            return (identity[0], identity[1] + 1, *identity[2:])
        return identity

    monkeypatch.setattr(benchmark_env, "_stat_identity", mismatched_identity)
    with pytest.raises(
        BenchmarkEnvironmentError,
        match="destination parent changed while opening",
    ):
        benchmark_env._open_materialization_parent(destination)


def test_materialization_parent_stability_rejects_fstat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination, descriptor, opened = benchmark_env._open_materialization_parent(
        tmp_path / "materialized"
    )
    original_fstat = os.fstat

    def failing_fstat(fd: int) -> os.stat_result:
        if fd == descriptor:
            raise PermissionError("blocked parent fstat")
        return original_fstat(fd)

    monkeypatch.setattr(os, "fstat", failing_fstat)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="unable to restat environment materialization destination parent",
        ):
            benchmark_env._materialization_parent_must_be_stable(
                destination,
                descriptor,
                opened,
            )
    finally:
        os.close(descriptor)


def test_open_bound_materialized_directory_rejects_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child"
    child.mkdir()
    descriptor = _directory_descriptor(parent)
    expected = child.stat(follow_symlinks=False)
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == child.name and kwargs.get("dir_fd") == descriptor:
            raise PermissionError("blocked child open")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)
    try:
        with pytest.raises(BenchmarkEnvironmentError, match="unable to open child directory"):
            benchmark_env._open_bound_materialized_directory(
                descriptor,
                child.name,
                expected,
                noun="child directory",
            )
    finally:
        os.close(descriptor)


def test_open_bound_materialized_directory_rejects_replacement(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    child = parent / "child"
    child.mkdir()
    expected = child.stat(follow_symlinks=False)
    child.rename(parent / "old-child")
    child.mkdir()
    descriptor = _directory_descriptor(parent)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="child directory changed while opening",
        ):
            benchmark_env._open_bound_materialized_directory(
                descriptor,
                child.name,
                expected,
                noun="child directory",
            )
    finally:
        os.close(descriptor)


def test_open_materialized_directory_rejects_missing_component(tmp_path: Path) -> None:
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


def test_open_materialized_directory_rejects_inspection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    descriptor = _directory_descriptor(root)

    def failing_stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
        raise PermissionError("blocked component stat")

    monkeypatch.setattr(benchmark_env, "_stat_materialization_entry", failing_stat_entry)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="unable to inspect materialized environment directory nested",
        ):
            benchmark_env._open_materialized_directory(
                descriptor,
                ("nested",),
                create=False,
            )
    finally:
        os.close(descriptor)


def test_remove_materialized_tree_ignores_missing_entry(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    descriptor = _directory_descriptor(parent)
    try:
        benchmark_env._remove_materialized_tree_at(descriptor, "missing")
    finally:
        os.close(descriptor)


def test_remove_materialized_tree_tolerates_child_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "materialized"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")
    descriptor = _directory_descriptor(parent)
    original_stat_entry = benchmark_env._stat_materialization_entry

    def failing_stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
        if name == "value.txt":
            raise PermissionError("blocked child stat")
        return original_stat_entry(parent_descriptor, name)

    monkeypatch.setattr(benchmark_env, "_stat_materialization_entry", failing_stat_entry)
    try:
        benchmark_env._remove_materialized_tree_at(descriptor, root.name)
    finally:
        os.close(descriptor)
    assert root.is_dir()


def test_remove_materialized_tree_tolerates_bound_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "materialized"
    root.mkdir()
    descriptor = _directory_descriptor(parent)

    def failing_open(*args: Any, **kwargs: Any) -> int:
        raise BenchmarkEnvironmentError("changed")

    monkeypatch.setattr(benchmark_env, "_open_bound_materialized_directory", failing_open)
    try:
        benchmark_env._remove_materialized_tree_at(descriptor, root.name)
    finally:
        os.close(descriptor)
    assert root.is_dir()


def test_resolve_under_source_rejects_missing_entry(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(BenchmarkEnvironmentError, match="unable to resolve environment entry"):
        benchmark_env._resolve_under_source(source, source / "missing", "missing")


def test_resolve_under_source_rejects_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    with pytest.raises(BenchmarkEnvironmentError, match="environment entry escapes source_dir"):
        benchmark_env._resolve_under_source(source, outside, "outside.txt")


def test_hash_environment_file_rejects_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    value = source / "value.txt"
    expected = value.stat(follow_symlinks=False)
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if Path(path) == value:
            raise PermissionError("blocked file open")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing_open)
    with pytest.raises(BenchmarkEnvironmentError, match="unable to open environment file"):
        benchmark_env._hash_environment_file(source, value, value.name, expected)


def test_hash_environment_file_rejects_non_regular_replacement(tmp_path: Path) -> None:
    source = _source(tmp_path)
    value = source / "value.txt"
    expected = value.stat(follow_symlinks=False)
    value.unlink()
    value.mkdir()
    with pytest.raises(BenchmarkEnvironmentError, match="environment contains non-regular file"):
        benchmark_env._hash_environment_file(source, value, value.name, expected)


def test_hash_environment_file_rejects_identity_replacement(tmp_path: Path) -> None:
    source = _source(tmp_path)
    value = source / "value.txt"
    expected = value.stat(follow_symlinks=False)
    value.unlink()
    value.write_text("replacement\n", encoding="utf-8")
    with pytest.raises(BenchmarkEnvironmentError, match="environment file changed while opening"):
        benchmark_env._hash_environment_file(source, value, value.name, expected)


def test_hash_environment_file_rejects_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    value = source / "value.txt"
    expected = value.stat(follow_symlinks=False)
    monkeypatch.setattr(benchmark_env, "_MAX_ENVIRONMENT_BYTES", 1)
    with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 1 total bytes"):
        benchmark_env._hash_environment_file(source, value, value.name, expected)


def test_hash_environment_file_rejects_restat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    value = source / "value.txt"
    expected = value.stat(follow_symlinks=False)
    original_stat = Path.stat

    def failing_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == value:
            raise PermissionError("blocked file restat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    with pytest.raises(BenchmarkEnvironmentError, match="unable to restat environment file"):
        benchmark_env._hash_environment_file(source, value, value.name, expected)


def test_scan_environment_rejects_source_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original_stat = Path.stat

    def failing_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == source:
            raise PermissionError("blocked source stat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    with pytest.raises(BenchmarkEnvironmentError, match="unable to stat environment source_dir"):
        benchmark_env._scan_environment(source)


def test_scan_environment_rejects_symlink_source(tmp_path: Path) -> None:
    actual = _source(tmp_path)
    source = tmp_path / "source-link"
    source.symlink_to(actual, target_is_directory=True)
    with pytest.raises(
        BenchmarkEnvironmentError,
        match="environment source_dir is not a directory",
    ):
        benchmark_env._scan_environment(source)


def test_scan_environment_rejects_entry_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    value = source / "value.txt"
    original_stat = Path.stat

    def failing_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == value:
            raise PermissionError("blocked entry stat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    with pytest.raises(BenchmarkEnvironmentError, match="unable to stat environment entry"):
        benchmark_env._scan_environment(source)


def test_scan_environment_rejects_symlink_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (source / "value.txt").symlink_to(outside)
    with pytest.raises(BenchmarkEnvironmentError, match="environment entry escapes source_dir"):
        benchmark_env._scan_environment(source)


def test_scan_environment_rejects_file_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    monkeypatch.setattr(benchmark_env, "_MAX_FILES", 0)
    with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 0 files"):
        benchmark_env._scan_environment(source)


def test_scan_environment_rejects_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    monkeypatch.setattr(benchmark_env, "_MAX_ENVIRONMENT_BYTES", 0)
    with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 0 total bytes"):
        benchmark_env._scan_environment(source)


def test_hash_materialized_file_rejects_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    value = root / "value.txt"
    value.write_text("inside\n", encoding="utf-8")
    expected = value.stat(follow_symlinks=False)
    descriptor = _directory_descriptor(root)
    original_open = os.open

    def failing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == value.name and kwargs.get("dir_fd") == descriptor:
            raise PermissionError("blocked materialized file open")
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


def test_hash_materialized_file_rejects_replacement(tmp_path: Path) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    value = root / "value.txt"
    value.write_text("inside\n", encoding="utf-8")
    expected = value.stat(follow_symlinks=False)
    value.unlink()
    value.write_text("replacement\n", encoding="utf-8")
    descriptor = _directory_descriptor(root)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="materialized environment file changed while opening",
        ):
            benchmark_env._hash_materialized_file(
                descriptor,
                value.name,
                value.name,
                expected,
            )
    finally:
        os.close(descriptor)


def test_hash_materialized_file_rejects_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    value = root / "value.txt"
    value.write_text("inside\n", encoding="utf-8")
    expected = value.stat(follow_symlinks=False)
    descriptor = _directory_descriptor(root)
    monkeypatch.setattr(benchmark_env, "_MAX_ENVIRONMENT_BYTES", 1)
    try:
        with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 1 total bytes"):
            benchmark_env._hash_materialized_file(
                descriptor,
                value.name,
                value.name,
                expected,
            )
    finally:
        os.close(descriptor)


def test_hash_materialized_file_rejects_post_hash_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    value = root / "value.txt"
    value.write_text("inside\n", encoding="utf-8")
    expected = value.stat(follow_symlinks=False)
    descriptor = _directory_descriptor(root)

    def failing_stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
        raise PermissionError("blocked post-hash stat")

    monkeypatch.setattr(benchmark_env, "_stat_materialization_entry", failing_stat_entry)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="unable to hash materialized environment file",
        ):
            benchmark_env._hash_materialized_file(
                descriptor,
                value.name,
                value.name,
                expected,
            )
    finally:
        os.close(descriptor)


def test_scan_materialized_environment_rejects_file_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")
    descriptor = _directory_descriptor(root)
    monkeypatch.setattr(benchmark_env, "_MAX_FILES", 0)
    try:
        with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 0 files"):
            benchmark_env._scan_materialized_environment(descriptor)
    finally:
        os.close(descriptor)


def test_scan_materialized_environment_rejects_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")
    descriptor = _directory_descriptor(root)
    monkeypatch.setattr(benchmark_env, "_MAX_ENVIRONMENT_BYTES", 0)
    try:
        with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 0 total bytes"):
            benchmark_env._scan_materialized_environment(descriptor)
    finally:
        os.close(descriptor)


def test_scan_materialized_environment_scans_nested_tree(tmp_path: Path) -> None:
    root = tmp_path / "materialized"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "value.txt").write_text("inside\n", encoding="utf-8")
    descriptor = _directory_descriptor(root)
    try:
        scanned = benchmark_env._scan_materialized_environment(descriptor)
    finally:
        os.close(descriptor)
    assert [item.path for item in scanned.files] == ["nested/value.txt"]


def test_descriptor_copy_rejects_destination_inspection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    original_stat_entry = benchmark_env._stat_materialization_entry

    def failing_stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
        if name == destination.name:
            raise PermissionError("blocked destination stat")
        return original_stat_entry(parent_descriptor, name)

    monkeypatch.setattr(benchmark_env, "_stat_materialization_entry", failing_stat_entry)
    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to inspect environment materialization destination",
    ):
        benchmark_env._copy_environment_tree(source, destination)


def test_descriptor_copy_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(BenchmarkEnvironmentError, match="unable to stat environment source_dir"):
        benchmark_env._copy_environment_tree(
            tmp_path / "missing-source",
            tmp_path / "materialized",
        )


def test_descriptor_copy_rejects_file_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    monkeypatch.setattr(benchmark_env, "_MAX_FILES", 0)
    with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 0 files"):
        benchmark_env._copy_environment_tree(source, destination)
    assert not destination.exists()


def test_descriptor_copy_rejects_byte_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    monkeypatch.setattr(benchmark_env, "_MAX_ENVIRONMENT_BYTES", 0)
    with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 0 total bytes"):
        benchmark_env._copy_environment_tree(source, destination)
    assert not destination.exists()


def test_copy_environment_file_at_rejects_source_restat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    value = source / "value.txt"
    expected = value.stat(follow_symlinks=False)
    root = tmp_path / "materialized"
    root.mkdir()
    root_descriptor = _directory_descriptor(root)
    original_stat = Path.stat

    def failing_stat(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == value:
            raise PermissionError("blocked source restat")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    try:
        with pytest.raises(BenchmarkEnvironmentError, match="unable to restat environment file"):
            benchmark_env._copy_environment_file_at(
                source,
                value,
                value.name,
                expected,
                root_descriptor,
                (value.name,),
            )
    finally:
        os.close(root_descriptor)


def test_copy_environment_file_at_rejects_source_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    value = source / "value.txt"
    expected = value.stat(follow_symlinks=False)
    root = tmp_path / "materialized"
    root.mkdir()
    root_descriptor = _directory_descriptor(root)
    monkeypatch.setattr(benchmark_env, "_MAX_ENVIRONMENT_BYTES", 1)
    try:
        with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 1 total bytes"):
            benchmark_env._copy_environment_file_at(
                source,
                value,
                value.name,
                expected,
                root_descriptor,
                (value.name,),
            )
    finally:
        os.close(root_descriptor)
