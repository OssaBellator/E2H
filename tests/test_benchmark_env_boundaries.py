from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

import e2h.benchmark_env as benchmark_env
from e2h.benchmark_env import (
    BenchmarkEnvironmentError,
    BenchmarkEnvironmentKind,
    BenchmarkEnvironmentSpec,
    BenchmarkEnvironmentSuite,
    BenchmarkEnvironmentSuiteLock,
    BenchmarkNetworkPolicy,
    materialize_benchmark_environment,
    seal_benchmark_environment_suite,
    verify_benchmark_environment_suite,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _SuiteSubclass(BenchmarkEnvironmentSuite):
    pass


class _LockSubclass(BenchmarkEnvironmentSuiteLock):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def _environment(
    *,
    identifier: str = "coding",
    kind: BenchmarkEnvironmentKind = BenchmarkEnvironmentKind.CODING,
    source_dir: str = "coding",
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
        description="Boundary fixture.",
    )


def _suite() -> BenchmarkEnvironmentSuite:
    return BenchmarkEnvironmentSuite(
        id="boundary-suite",
        title="Boundary suite",
        environments=[_environment()],
    )


def _multi_suite() -> BenchmarkEnvironmentSuite:
    return BenchmarkEnvironmentSuite(
        id="multi-boundary-suite",
        title="Multi boundary suite",
        environments=[
            _environment(),
            _environment(
                identifier="research",
                kind=BenchmarkEnvironmentKind.RESEARCH,
                source_dir="research",
            ),
        ],
    )


def _root(tmp_path: Path, *, include_research: bool = False) -> Path:
    root = tmp_path / "root"
    coding = root / "coding"
    coding.mkdir(parents=True)
    (coding / "check.py").write_text("print('coding')\n", encoding="utf-8")
    if include_research:
        research = root / "research"
        research.mkdir()
        (research / "check.py").write_text("print('research')\n", encoding="utf-8")
    return root


def test_seal_revalidates_unsafe_source_dir_before_traversal(tmp_path: Path) -> None:
    suite = _suite()
    suite.environments[0].source_dir = "../escape"

    with pytest.raises(BenchmarkEnvironmentError, match="invalid benchmark environment suite"):
        seal_benchmark_environment_suite(suite, root=_root(tmp_path))


def test_seal_revalidates_environment_network_contract(tmp_path: Path) -> None:
    suite = _suite()
    suite.environments[0].network = BenchmarkNetworkPolicy.LOCALHOST_ONLY

    with pytest.raises(BenchmarkEnvironmentError, match="coding and research environments"):
        seal_benchmark_environment_suite(suite, root=_root(tmp_path))


@pytest.mark.parametrize("mutation", ["id", "kind"])
def test_seal_revalidates_suite_uniqueness(tmp_path: Path, mutation: str) -> None:
    suite = _multi_suite()
    if mutation == "id":
        suite.environments[1].id = suite.environments[0].id
        expected = "ids must be unique"
    else:
        suite.environments[1].kind = suite.environments[0].kind
        expected = "kinds must be unique"

    with pytest.raises(BenchmarkEnvironmentError, match=expected):
        seal_benchmark_environment_suite(suite, root=_root(tmp_path, include_research=True))


@pytest.mark.parametrize("field", ["file_count", "total_bytes", "source_sha256"])
def test_verify_revalidates_lock_entry_summaries(tmp_path: Path, field: str) -> None:
    suite = _suite()
    root = _root(tmp_path)
    lock = seal_benchmark_environment_suite(suite, root=root)
    entry = lock.environments[0]
    if field == "file_count":
        entry.file_count += 1
        expected = "file_count does not match"
    elif field == "total_bytes":
        entry.total_bytes += 1
        expected = "total_bytes does not match"
    else:
        entry.source_sha256 = "0" * 64
        expected = "source_sha256 does not match"

    with pytest.raises(BenchmarkEnvironmentError, match=expected):
        verify_benchmark_environment_suite(suite, lock, root=root)


def test_boundaries_reject_suite_and_lock_subclasses_and_lookalikes(tmp_path: Path) -> None:
    suite = _suite()
    root = _root(tmp_path)
    lock = seal_benchmark_environment_suite(suite, root=root)
    suite_subclass = _SuiteSubclass.model_validate(suite.model_dump(mode="json"))
    lock_subclass = _LockSubclass.model_validate(lock.model_dump(mode="json"))
    suite_lookalike = _Lookalike.model_validate(suite.model_dump(mode="json"))
    lock_lookalike = _Lookalike.model_validate(lock.model_dump(mode="json"))

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="expected BenchmarkEnvironmentSuite, got _SuiteSubclass",
    ):
        seal_benchmark_environment_suite(suite_subclass, root=root)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="expected BenchmarkEnvironmentSuite, got _Lookalike",
    ):
        seal_benchmark_environment_suite(cast(Any, suite_lookalike), root=root)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="expected BenchmarkEnvironmentSuiteLock, got _LockSubclass",
    ):
        verify_benchmark_environment_suite(suite, lock_subclass, root=root)

    with pytest.raises(
        BenchmarkEnvironmentError,
        match="expected BenchmarkEnvironmentSuiteLock, got _Lookalike",
    ):
        verify_benchmark_environment_suite(suite, cast(Any, lock_lookalike), root=root)


def test_boundaries_normalize_warning_prone_raw_nested_assignments(tmp_path: Path) -> None:
    suite = _suite()
    root = _root(tmp_path)
    lock = seal_benchmark_environment_suite(suite, root=root)
    suite.environments = [suite.environments[0].model_dump(mode="json")]
    lock.environments = [lock.environments[0].model_dump(mode="json")]

    verification = verify_benchmark_environment_suite(suite, lock, root=root)

    assert verification.verified is True
    assert verification.environment_count == 1


def test_materialize_uses_detached_suite_and_lock_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suite = _suite()
    root = _root(tmp_path)
    lock = seal_benchmark_environment_suite(suite, root=root)
    destination = tmp_path / "materialized"
    original_copytree = shutil.copytree

    def mutating_copytree(source: Path, target: Path, **kwargs: Any) -> str:
        suite.environments[0].source_dir = "../caller-mutated"
        lock.environments[0].source_sha256 = "0" * 64
        result = original_copytree(source, target, **kwargs)
        return str(result)

    monkeypatch.setattr(benchmark_env.shutil, "copytree", mutating_copytree)

    verification = materialize_benchmark_environment(
        suite,
        lock,
        "coding",
        root=root,
        destination=destination,
    )

    assert verification.verified is True
    assert (destination / "check.py").read_text(encoding="utf-8") == "print('coding')\n"
