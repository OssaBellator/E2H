from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from e2h.benchmark_env import (
    BenchmarkEnvironmentError,
    BenchmarkEnvironmentKind,
    BenchmarkEnvironmentSpec,
    BenchmarkEnvironmentSuite,
    BenchmarkNetworkPolicy,
    benchmark_environment_suite_sha256,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _SuiteSubclass(BenchmarkEnvironmentSuite):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def _environment(
    *,
    identifier: str,
    kind: BenchmarkEnvironmentKind,
    source_dir: str,
) -> BenchmarkEnvironmentSpec:
    return BenchmarkEnvironmentSpec(
        id=identifier,
        kind=kind,
        source_dir=source_dir,
        network=(
            BenchmarkNetworkPolicy.LOCALHOST_ONLY
            if kind is BenchmarkEnvironmentKind.BROWSER
            else BenchmarkNetworkPolicy.NONE
        ),
        entrypoint=["python", "check.py"],
        candidate_artifact="answer.txt",
        description="Digest boundary fixture.",
    )


def _suite() -> BenchmarkEnvironmentSuite:
    return BenchmarkEnvironmentSuite(
        id="digest-boundary-suite",
        title="Digest boundary suite",
        environments=[
            _environment(
                identifier="coding",
                kind=BenchmarkEnvironmentKind.CODING,
                source_dir="coding",
            ),
            _environment(
                identifier="research",
                kind=BenchmarkEnvironmentKind.RESEARCH,
                source_dir="research",
            ),
        ],
    )


@pytest.mark.parametrize("field", ["id", "kind"])
def test_suite_digest_revalidates_uniqueness(field: str) -> None:
    candidate = _suite()
    if field == "id":
        candidate.environments[1].id = candidate.environments[0].id
        expected = "ids must be unique"
    else:
        candidate.environments[1].kind = candidate.environments[0].kind
        expected = "kinds must be unique"

    with pytest.raises(BenchmarkEnvironmentError, match=expected):
        benchmark_environment_suite_sha256(candidate)


def test_suite_digest_revalidates_nested_network_contract() -> None:
    candidate = _suite()
    candidate.environments[0].network = BenchmarkNetworkPolicy.LOCALHOST_ONLY

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="coding and research environments must use network policy none",
    ):
        benchmark_environment_suite_sha256(candidate)


def test_suite_digest_rejects_subclass_and_lookalike() -> None:
    candidate = _suite()
    subclassed = _SuiteSubclass.model_validate(candidate.model_dump(mode="json"))
    lookalike = _Lookalike.model_validate(candidate.model_dump(mode="json"))

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="expected BenchmarkEnvironmentSuite, got _SuiteSubclass",
    ):
        benchmark_environment_suite_sha256(subclassed)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="expected BenchmarkEnvironmentSuite, got _Lookalike",
    ):
        benchmark_environment_suite_sha256(cast(Any, lookalike))


def test_suite_digest_normalizes_warning_prone_raw_nested_assignment() -> None:
    candidate = _suite()
    expected = benchmark_environment_suite_sha256(candidate)
    candidate.environments = [
        environment.model_dump(mode="json") for environment in candidate.environments
    ]

    assert benchmark_environment_suite_sha256(candidate) == expected
