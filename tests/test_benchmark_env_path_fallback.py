from __future__ import annotations

from pathlib import Path

import pytest

import e2h.benchmark_env as benchmark_env
from e2h.benchmark_env import BenchmarkEnvironmentError


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "value.txt").write_text("inside\n", encoding="utf-8")
    (nested / "second.txt").write_text("second\n", encoding="utf-8")
    return source


def test_path_fallback_copies_nested_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    monkeypatch.setattr(benchmark_env, "_MATERIALIZATION_DIR_FD_SUPPORTED", False)

    scanned = benchmark_env._copy_environment_tree(source, destination)

    assert [item.path for item in scanned.files] == ["nested/second.txt", "value.txt"]
    assert (destination / "value.txt").read_text(encoding="utf-8") == "inside\n"
    assert (destination / "nested" / "second.txt").read_text(encoding="utf-8") == "second\n"


def test_path_fallback_rejects_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    destination.mkdir()
    monkeypatch.setattr(benchmark_env, "_MATERIALIZATION_DIR_FD_SUPPORTED", False)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="environment materialization destination already exists",
    ):
        benchmark_env._copy_environment_tree(source, destination)


def test_path_fallback_rejects_missing_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark_env, "_MATERIALIZATION_DIR_FD_SUPPORTED", False)

    with pytest.raises(BenchmarkEnvironmentError, match="unable to stat environment source_dir"):
        benchmark_env._copy_environment_tree(
            tmp_path / "missing-source",
            tmp_path / "materialized",
        )


def test_path_fallback_rejects_symlink_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = _source(tmp_path)
    source = tmp_path / "source-link"
    source.symlink_to(actual, target_is_directory=True)
    monkeypatch.setattr(benchmark_env, "_MATERIALIZATION_DIR_FD_SUPPORTED", False)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="environment source_dir is not a directory",
    ):
        benchmark_env._copy_environment_tree(source, tmp_path / "materialized")


def test_path_fallback_rejects_empty_source_and_cleans_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "materialized"
    monkeypatch.setattr(benchmark_env, "_MATERIALIZATION_DIR_FD_SUPPORTED", False)

    with pytest.raises(BenchmarkEnvironmentError, match="environment source_dir contains no files"):
        benchmark_env._copy_environment_tree(source, destination)

    assert not destination.exists()


def test_path_fallback_rejects_file_limit_and_cleans_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    monkeypatch.setattr(benchmark_env, "_MATERIALIZATION_DIR_FD_SUPPORTED", False)
    monkeypatch.setattr(benchmark_env, "_MAX_FILES", 0)

    with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 0 files"):
        benchmark_env._copy_environment_tree(source, destination)

    assert not destination.exists()


def test_path_fallback_rejects_byte_limit_and_cleans_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "materialized"
    monkeypatch.setattr(benchmark_env, "_MATERIALIZATION_DIR_FD_SUPPORTED", False)
    monkeypatch.setattr(benchmark_env, "_MAX_ENVIRONMENT_BYTES", 0)

    with pytest.raises(BenchmarkEnvironmentError, match="environment exceeds 0 total bytes"):
        benchmark_env._copy_environment_tree(source, destination)

    assert not destination.exists()


def test_path_fallback_rejects_parent_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    parent = tmp_path / "not-a-directory"
    parent.write_text("file\n", encoding="utf-8")
    monkeypatch.setattr(benchmark_env, "_MATERIALIZATION_DIR_FD_SUPPORTED", False)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to prepare environment materialization destination",
    ):
        benchmark_env._copy_environment_tree(source, parent / "materialized")
