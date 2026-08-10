"""Regression coverage for controlled benchmark-environment resolver failures."""

from __future__ import annotations

from pathlib import Path

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
        id="suite",
        title="Suite",
        environments=[
            BenchmarkEnvironmentSpec(
                id="coding",
                kind=BenchmarkEnvironmentKind.CODING,
                source_dir="environment",
                network=BenchmarkNetworkPolicy.NONE,
                entrypoint=["python", "candidate.py"],
                candidate_artifact="candidate.py",
                description="Coding environment",
            )
        ],
    )


def test_benchmark_environment_root_resolution_failure_is_normalized() -> None:
    class BrokenPath(type(Path())):
        def resolve(self, strict: bool = False) -> Path:
            del strict
            raise RuntimeError("resolution loop")

    with pytest.raises(BenchmarkEnvironmentError, match="unable to resolve benchmark environment root"):
        seal_benchmark_environment_suite(_suite(), root=BrokenPath("root"))


def test_benchmark_environment_source_resolution_failure_is_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "environment").mkdir()
    original = type(tmp_path).resolve

    def broken_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if path.name == "environment":
            raise OSError("resolution failed")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(tmp_path), "resolve", broken_resolve)

    with pytest.raises(BenchmarkEnvironmentError, match="unable to resolve environment source_dir"):
        seal_benchmark_environment_suite(_suite(), root=tmp_path)
