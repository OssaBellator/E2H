from __future__ import annotations

from pathlib import Path

import pytest

import e2h.benchmark_env as benchmark_env
from e2h.benchmark_env import BenchmarkEnvironmentError


def _raise_resolution_error(*args: object, **kwargs: object) -> Path:
    del args, kwargs
    raise RuntimeError("symlink loop")


def test_materialization_parent_normalizes_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to prepare environment materialization destination: symlink loop",
    ):
        benchmark_env._open_materialization_parent(tmp_path / "materialized")


def test_environment_entry_normalizes_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to resolve environment entry file.txt: symlink loop",
    ):
        benchmark_env._resolve_under_source(tmp_path, tmp_path / "file.txt", "file.txt")


def test_path_materialization_normalizes_resolve_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "resolve", _raise_resolution_error)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="unable to prepare environment materialization destination: symlink loop",
    ):
        benchmark_env._copy_environment_tree_path(
            tmp_path / "source",
            tmp_path / "materialized",
            expected=None,
        )
