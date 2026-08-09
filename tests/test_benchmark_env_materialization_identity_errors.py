from __future__ import annotations

import os
from pathlib import Path

import pytest

import e2h.benchmark_env as benchmark_env
from e2h.benchmark_env import BenchmarkEnvironmentError

pytestmark = pytest.mark.skipif(
    not benchmark_env._MATERIALIZATION_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative benchmark materialization support",
)


def _directory_descriptor(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _inode(path: Path) -> tuple[int, int]:
    info = path.stat(follow_symlinks=False)
    return (info.st_dev, info.st_ino)


def test_root_stability_normalizes_descriptor_restat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "materialized"
    root.mkdir()
    parent_descriptor = _directory_descriptor(parent)
    root_descriptor = _directory_descriptor(root)
    expected = root.stat(follow_symlinks=False)
    original_fstat = os.fstat

    def failing_fstat(descriptor: int) -> os.stat_result:
        if descriptor == root_descriptor:
            raise PermissionError("blocked root restat")
        return original_fstat(descriptor)

    monkeypatch.setattr(os, "fstat", failing_fstat)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="unable to restat environment materialization destination",
        ):
            benchmark_env._materialization_root_must_be_stable(
                parent_descriptor,
                root.name,
                root_descriptor,
                expected,
            )
    finally:
        os.close(root_descriptor)
        os.close(parent_descriptor)


def test_open_materialized_directory_normalizes_post_create_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "materialized"
    root.mkdir()
    descriptor = _directory_descriptor(root)
    original_stat_entry = benchmark_env._stat_materialization_entry
    calls = 0

    def failing_stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
        nonlocal calls
        if name == "nested":
            calls += 1
            if calls == 2:
                raise PermissionError("blocked post-create stat")
        return original_stat_entry(parent_descriptor, name)

    monkeypatch.setattr(benchmark_env, "_stat_materialization_entry", failing_stat_entry)
    try:
        with pytest.raises(
            BenchmarkEnvironmentError,
            match="unable to inspect materialized environment directory nested",
        ):
            benchmark_env._open_materialized_directory(
                descriptor,
                ("nested",),
                create=True,
            )
    finally:
        os.close(descriptor)

    assert calls == 2
    assert (root / "nested").is_dir()


def test_identity_bound_tree_removal_ignores_name_replacement(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "materialized"
    root.mkdir()
    expected_identity = _inode(root)
    root.rename(parent / "original")
    root.mkdir()
    (root / "replacement.txt").write_text("replacement\n", encoding="utf-8")
    descriptor = _directory_descriptor(parent)
    try:
        benchmark_env._remove_materialized_tree_at(
            descriptor,
            root.name,
            expected_identity=expected_identity,
        )
    finally:
        os.close(descriptor)

    assert (root / "replacement.txt").read_text(encoding="utf-8") == "replacement\n"
    assert (parent / "original").is_dir()


def test_identity_bound_tree_removal_does_not_unlink_non_directory(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    loose = parent / "materialized"
    loose.write_text("keep me\n", encoding="utf-8")
    descriptor = _directory_descriptor(parent)
    try:
        benchmark_env._remove_materialized_tree_at(
            descriptor,
            loose.name,
            expected_identity=_inode(loose),
        )
    finally:
        os.close(descriptor)

    assert loose.read_text(encoding="utf-8") == "keep me\n"


def test_identity_search_tolerates_parent_list_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    root = parent / "materialized"
    root.mkdir()
    descriptor = _directory_descriptor(parent)
    original_listdir = os.listdir

    def failing_listdir(path: object) -> list[str]:
        if path == descriptor:
            raise PermissionError("blocked parent list")
        return original_listdir(path)

    monkeypatch.setattr(os, "listdir", failing_listdir)
    try:
        benchmark_env._remove_materialized_tree_by_identity_at(descriptor, _inode(root))
    finally:
        os.close(descriptor)

    assert root.is_dir()


def test_identity_search_skips_unstatable_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    blocked = parent / "blocked"
    blocked.mkdir()
    root = parent / "materialized"
    root.mkdir()
    (root / "value.txt").write_text("inside\n", encoding="utf-8")
    descriptor = _directory_descriptor(parent)
    original_stat_entry = benchmark_env._stat_materialization_entry

    def failing_stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
        if parent_descriptor == descriptor and name == blocked.name:
            raise PermissionError("blocked sibling stat")
        return original_stat_entry(parent_descriptor, name)

    monkeypatch.setattr(benchmark_env, "_stat_materialization_entry", failing_stat_entry)
    try:
        benchmark_env._remove_materialized_tree_by_identity_at(descriptor, _inode(root))
    finally:
        os.close(descriptor)

    assert blocked.is_dir()
    assert not root.exists()


def test_descriptor_copy_normalizes_post_create_root_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "value.txt").write_text("inside\n", encoding="utf-8")
    destination = tmp_path / "materialized"
    original_stat_entry = benchmark_env._stat_materialization_entry
    calls = 0

    def failing_stat_entry(parent_descriptor: int, name: str) -> os.stat_result:
        nonlocal calls
        if name == destination.name:
            calls += 1
            if calls == 2:
                raise PermissionError("blocked created-root stat")
        return original_stat_entry(parent_descriptor, name)

    monkeypatch.setattr(benchmark_env, "_stat_materialization_entry", failing_stat_entry)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to inspect environment materialization destination after creation",
    ):
        benchmark_env._copy_environment_tree(source, destination)

    assert calls == 2
    assert destination.is_dir()
