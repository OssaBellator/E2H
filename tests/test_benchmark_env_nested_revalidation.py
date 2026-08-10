from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.benchmark_env import (
    BenchmarkEnvironmentKind,
    BenchmarkEnvironmentSpec,
    BenchmarkEnvironmentSuite,
    BenchmarkNetworkPolicy,
)


def _environment() -> BenchmarkEnvironmentSpec:
    return BenchmarkEnvironmentSpec(
        id="coding",
        kind=BenchmarkEnvironmentKind.CODING,
        source_dir="environment",
        network=BenchmarkNetworkPolicy.NONE,
        entrypoint=["python", "main.py"],
        candidate_artifact="candidate.json",
        description="Coding benchmark environment.",
    )


def test_suite_revalidates_mutated_environment_source_dir() -> None:
    environment = _environment()
    environment.source_dir = "../escape"

    with pytest.raises(ValidationError, match="parent segments"):
        BenchmarkEnvironmentSuite(
            id="suite",
            title="Suite",
            environments=[environment],
        )


def test_suite_revalidates_mutated_environment_entrypoint() -> None:
    environment = _environment()
    environment.entrypoint = ["python", "bad\x00arg"]

    with pytest.raises(ValidationError, match="entrypoint arguments"):
        BenchmarkEnvironmentSuite(
            id="suite",
            title="Suite",
            environments=[environment],
        )
