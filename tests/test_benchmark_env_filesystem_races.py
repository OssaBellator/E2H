from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import e2h.benchmark_env as benchmark_env
from e2h.benchmark_env import (
    BenchmarkEnvironmentError,
    BenchmarkEnvironmentKind,
    BenchmarkEnvironmentSpec,
    BenchmarkEnvironmentSuite,
    BenchmarkNetworkPolicy,
    materialize_benchmark_environment,
    seal_benchmark_environment_suite,
)


def _suite() -> BenchmarkEnvironmentSuite:
    return BenchmarkEnvironmentSuite(
        id="filesystem-race-suite",
        title="Filesystem race suite",
        environments=[
            BenchmarkEnvironmentSpec(
                id="coding",
                kind=BenchmarkEnvironmentKind.CODING,
                source_dir="coding",
                network=BenchmarkNetworkPolicy.NONE,
                entrypoint=["python", "check.py"],
                candidate_artifact="answer.txt",
                description="Exercise the environment hashing race boundary.",
            )
        ],
    )


def test_seal_rejects_file_swapped_to_symlink_during_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    source = root / "coding"
    source.mkdir(parents=True)
    victim = source / "check.py"
    victim.write_text("print('inside')\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path) == victim and not swapped:
            swapped = True
            victim.unlink()
            victim.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(BenchmarkEnvironmentError, match="environment file"):
        seal_benchmark_environment_suite(_suite(), root=root)

    assert swapped is True


def test_seal_rejects_directory_swapped_to_outside_symlink_during_listing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    nested = root / "coding" / "nested"
    nested.mkdir(parents=True)
    inside = nested / "inside.txt"
    inside.write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "other.txt").write_text("outside\n", encoding="utf-8")

    original_open = os.open
    swapped = False

    def swapping_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal swapped
        if Path(path) == nested and not swapped:
            swapped = True
            inside.unlink()
            nested.rmdir()
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapping_open)

    with pytest.raises(BenchmarkEnvironmentError, match="environment directory"):
        seal_benchmark_environment_suite(_suite(), root=root)

    assert swapped is True


def test_materialize_rejects_directory_replaced_after_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    nested = root / "coding" / "nested"
    nested.mkdir(parents=True)
    inside = nested / "inside.txt"
    inside.write_text("inside\n", encoding="utf-8")
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    (alternate / "other.txt").write_text("alternate\n", encoding="utf-8")
    suite = _suite()
    lock = seal_benchmark_environment_suite(suite, root=root)
    destination = tmp_path / "materialized"

    original_open = os.open
    nested_opens = 0
    replaced = False

    def replacing_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        nonlocal nested_opens, replaced
        if Path(path) == nested:
            nested_opens += 1
            if nested_opens == 2 and not replaced:
                replaced = True
                inside.unlink()
                nested.rmdir()
                nested.symlink_to(alternate, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replacing_open)

    with pytest.raises(BenchmarkEnvironmentError, match="environment directory"):
        materialize_benchmark_environment(
            suite,
            lock,
            "coding",
            root=root,
            destination=destination,
        )

    assert replaced is True
    assert not destination.exists()


def test_materialize_rejects_dangling_destination_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    source = root / "coding"
    source.mkdir(parents=True)
    (source / "check.py").write_text("print('inside')\n", encoding="utf-8")
    suite = _suite()
    lock = seal_benchmark_environment_suite(suite, root=root)

    outside = tmp_path / "outside-materialized"
    destination = tmp_path / "materialized"
    destination.symlink_to(outside, target_is_directory=True)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="environment materialization destination already exists",
    ):
        materialize_benchmark_environment(
            suite,
            lock,
            "coding",
            root=root,
            destination=destination,
        )

    assert destination.is_symlink()
    assert not outside.exists()


@pytest.mark.skipif(
    not benchmark_env._MATERIALIZATION_DIR_FD_SUPPORTED,
    reason="requires descriptor-relative benchmark materialization support",
)
def test_materialize_rejects_destination_parent_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    source = root / "coding"
    source.mkdir(parents=True)
    (source / "check.py").write_text("print('inside')\n", encoding="utf-8")
    suite = _suite()
    lock = seal_benchmark_environment_suite(suite, root=root)

    destination_parent = tmp_path / "destination-parent"
    destination_parent.mkdir()
    destination = destination_parent / "materialized"
    moved = tmp_path / "original-destination-parent"
    outside = tmp_path / "outside-destination-parent"
    outside.mkdir()

    original_mkdir = os.mkdir
    swapped = False

    def swapping_mkdir(
        path: Any,
        mode: int = 0o777,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        nonlocal swapped
        if Path(path).name == destination.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            destination_parent.rename(moved)
            destination_parent.symlink_to(outside, target_is_directory=True)
        original_mkdir(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", swapping_mkdir)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="environment materialization destination parent changed while writing",
    ):
        materialize_benchmark_environment(
            suite,
            lock,
            "coding",
            root=root,
            destination=destination,
        )

    assert swapped is True
    assert list(outside.iterdir()) == []
    assert not (moved / destination.name).exists()
