from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from e2h.benchmark_env import (
    BenchmarkEnvironmentError,
    BenchmarkEnvironmentKind,
    BenchmarkEnvironmentSpec,
    BenchmarkEnvironmentSuite,
    BenchmarkNetworkPolicy,
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
    outside.write_text("print('outside-secret')\n", encoding="utf-8")

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
