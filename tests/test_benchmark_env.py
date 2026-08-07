from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from e2h.benchmark_cli import benchmark_app
from e2h.benchmark_env import (
    BenchmarkEnvironmentError,
    BenchmarkEnvironmentKind,
    BenchmarkEnvironmentSpec,
    BenchmarkEnvironmentSuite,
    BenchmarkNetworkPolicy,
    load_benchmark_environment_lock,
    load_benchmark_environment_suite,
    materialize_benchmark_environment,
    seal_benchmark_environment_suite,
    verify_benchmark_environment_suite,
)
from e2h.benchmark_env_cli import environments_app

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "benchmarks" / "environments" / "suite.json"
LOCK_PATH = ROOT / "benchmarks" / "environments" / "suite.lock.json"


def test_seed_suite_covers_all_environment_kinds() -> None:
    suite = load_benchmark_environment_suite(SUITE_PATH)
    assert [environment.kind for environment in suite.environments] == [
        BenchmarkEnvironmentKind.CODING,
        BenchmarkEnvironmentKind.RESEARCH,
        BenchmarkEnvironmentKind.BROWSER,
    ]
    assert [environment.network for environment in suite.environments] == [
        BenchmarkNetworkPolicy.NONE,
        BenchmarkNetworkPolicy.NONE,
        BenchmarkNetworkPolicy.LOCALHOST_ONLY,
    ]


def test_seal_is_deterministic_and_lock_verifies() -> None:
    suite = load_benchmark_environment_suite(SUITE_PATH)
    first = seal_benchmark_environment_suite(suite, root=ROOT)
    second = seal_benchmark_environment_suite(suite, root=ROOT)
    assert first == second
    assert len(first.environments) == 3
    assert all(len(environment.source_sha256) == 64 for environment in first.environments)
    verification = verify_benchmark_environment_suite(suite, first, root=ROOT)
    assert verification.verified is True
    assert verification.environment_count == 3
    assert verification.file_count >= 10
    assert verification.total_bytes > 0


def test_committed_lock_matches_seed_sources() -> None:
    suite = load_benchmark_environment_suite(SUITE_PATH)
    lock = load_benchmark_environment_lock(LOCK_PATH)
    verification = verify_benchmark_environment_suite(suite, lock, root=ROOT)
    assert verification.verified is True
    assert set(verification.environment_sha256) == {
        "coding-python-normalizer",
        "research-local-evidence",
        "browser-static-release",
    }


def test_environment_spec_enforces_portable_paths_and_network_policy() -> None:
    with pytest.raises(ValidationError, match="source_dir"):
        BenchmarkEnvironmentSpec(
            id="bad-path",
            kind="coding",
            source_dir="../escape",
            network="none",
            entrypoint=["python", "checks/check.py"],
            candidate_artifact="answer.json",
            description="Invalid path.",
        )
    with pytest.raises(ValidationError, match="browser environments"):
        BenchmarkEnvironmentSpec(
            id="bad-browser-network",
            kind="browser",
            source_dir="browser",
            network="none",
            entrypoint=["python", "-m", "http.server"],
            candidate_artifact="result.json",
            description="Invalid browser network boundary.",
        )
    with pytest.raises(ValidationError, match="coding and research"):
        BenchmarkEnvironmentSpec(
            id="bad-coding-network",
            kind="coding",
            source_dir="coding",
            network="localhost_only",
            entrypoint=["python", "checks/check.py"],
            candidate_artifact="src/task.py",
            description="Invalid coding network boundary.",
        )


def test_suite_rejects_duplicate_environment_kinds() -> None:
    first = BenchmarkEnvironmentSpec(
        id="first",
        kind="coding",
        source_dir="first",
        network="none",
        entrypoint=["python", "check.py"],
        candidate_artifact="answer.txt",
        description="First coding environment.",
    )
    second = first.model_copy(update={"id": "second", "source_dir": "second"})
    with pytest.raises(ValidationError, match="kinds must be unique"):
        BenchmarkEnvironmentSuite(
            id="duplicates",
            title="Duplicate kinds",
            environments=[first, second],
        )


def test_tampered_source_fails_locked_verification(tmp_path: Path) -> None:
    suite = load_benchmark_environment_suite(SUITE_PATH)
    lock = seal_benchmark_environment_suite(suite, root=ROOT)
    copied_root = tmp_path / "root"
    copied_root.mkdir()
    source = ROOT / "benchmarks" / "environments"
    target = copied_root / "benchmarks" / "environments"
    target.parent.mkdir(parents=True)
    import shutil

    shutil.copytree(source, target)
    task = target / "coding-python" / "src" / "task.py"
    task.write_text(task.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(BenchmarkEnvironmentError, match="digest does not match"):
        verify_benchmark_environment_suite(suite, lock, root=copied_root)


def test_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    root = tmp_path / "root"
    environment = root / "env"
    environment.mkdir(parents=True)
    target = environment / "data.txt"
    target.write_text("data", encoding="utf-8")
    link = environment / "link.txt"
    try:
        link.symlink_to(target.name)
    except OSError:
        pytest.skip("symlink creation unavailable")
    suite = BenchmarkEnvironmentSuite(
        id="symlink-suite",
        title="Symlink suite",
        environments=[
            BenchmarkEnvironmentSpec(
                id="coding",
                kind="coding",
                source_dir="env",
                network="none",
                entrypoint=["python", "check.py"],
                candidate_artifact="answer.txt",
                description="Symlink rejection fixture.",
            )
        ],
    )
    with pytest.raises(BenchmarkEnvironmentError, match="symlink"):
        seal_benchmark_environment_suite(suite, root=root)


def test_materialize_copies_exact_locked_tree(tmp_path: Path) -> None:
    suite = load_benchmark_environment_suite(SUITE_PATH)
    lock = seal_benchmark_environment_suite(suite, root=ROOT)
    destination = tmp_path / "coding"
    materialize_benchmark_environment(
        suite,
        lock,
        "coding-python-normalizer",
        root=ROOT,
        destination=destination,
    )
    assert (destination / "README.md").is_file()
    assert (destination / "checks" / "check.py").is_file()
    with pytest.raises(BenchmarkEnvironmentError, match="already exists"):
        materialize_benchmark_environment(
            suite,
            lock,
            "coding-python-normalizer",
            root=ROOT,
            destination=destination,
        )


def _run_checker(environment: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "checks/check.py"],
        cwd=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_coding_environment_checker_accepts_valid_candidate(tmp_path: Path) -> None:
    suite = load_benchmark_environment_suite(SUITE_PATH)
    lock = seal_benchmark_environment_suite(suite, root=ROOT)
    environment = tmp_path / "coding"
    materialize_benchmark_environment(
        suite,
        lock,
        "coding-python-normalizer",
        root=ROOT,
        destination=environment,
    )
    (environment / "src" / "task.py").write_text(
        "import re\n"
        "\n"
        "def normalize_identifier(value: str) -> str:\n"
        "    result = re.sub(r'[\\s_-]+', '-', value.strip()).strip('-').lower()\n"
        "    if not result:\n"
        "        raise ValueError('normalized identifier is empty')\n"
        "    return result\n",
        encoding="utf-8",
    )
    result = _run_checker(environment)
    assert result.returncode == 0, result.stderr
    assert "coding-environment-ok" in result.stdout


def test_research_environment_checker_accepts_supported_answer(tmp_path: Path) -> None:
    suite = load_benchmark_environment_suite(SUITE_PATH)
    lock = seal_benchmark_environment_suite(suite, root=ROOT)
    environment = tmp_path / "research"
    materialize_benchmark_environment(
        suite,
        lock,
        "research-local-evidence",
        root=ROOT,
        destination=environment,
    )
    (environment / "answer.json").write_text(
        json.dumps(
            {
                "project": "Project Alder",
                "days": 16,
                "sources": ["source-a", "source-b"],
            }
        ),
        encoding="utf-8",
    )
    result = _run_checker(environment)
    assert result.returncode == 0, result.stderr
    assert "research-environment-ok" in result.stdout


def test_browser_environment_checker_accepts_observed_result(tmp_path: Path) -> None:
    suite = load_benchmark_environment_suite(SUITE_PATH)
    lock = seal_benchmark_environment_suite(suite, root=ROOT)
    environment = tmp_path / "browser"
    materialize_benchmark_environment(
        suite,
        lock,
        "browser-static-release",
        root=ROOT,
        destination=environment,
    )
    (environment / "result.json").write_text(
        json.dumps(
            {
                "status": "complete",
                "target": "release-2026-08",
                "path": ["home", "details"],
            }
        ),
        encoding="utf-8",
    )
    result = _run_checker(environment)
    assert result.returncode == 0, result.stderr
    assert "browser-environment-ok" in result.stdout


def test_cli_seal_verify_materialize_and_schema(tmp_path: Path) -> None:
    lock_path = tmp_path / "lock.json"
    seal = runner.invoke(
        environments_app,
        ["seal", str(SUITE_PATH), "--root", str(ROOT), "--output", str(lock_path)],
    )
    assert seal.exit_code == 0

    verify = runner.invoke(
        environments_app,
        ["verify", str(SUITE_PATH), str(lock_path), "--root", str(ROOT), "--json"],
    )
    assert verify.exit_code == 0
    payload = json.loads(verify.stdout)
    assert payload["verified"] is True
    assert payload["environment_count"] == 3

    destination = tmp_path / "materialized"
    materialize = runner.invoke(
        environments_app,
        [
            "materialize",
            str(SUITE_PATH),
            str(lock_path),
            "research-local-evidence",
            str(destination),
            "--root",
            str(ROOT),
        ],
    )
    assert materialize.exit_code == 0
    assert (destination / "sources" / "source-a.txt").is_file()

    for kind in ("suite", "lock", "verification"):
        schema = runner.invoke(environments_app, ["schema", "--kind", kind])
        assert schema.exit_code == 0
        assert json.loads(schema.stdout)["type"] == "object"


def test_environment_loader_rejects_duplicate_mapping_keys(tmp_path: Path) -> None:
    source = tmp_path / "duplicate.json"
    source.write_text('{"schema_version":"0.1","id":"one","id":"two"}', encoding="utf-8")
    with pytest.raises(BenchmarkEnvironmentError, match="duplicate object key"):
        load_benchmark_environment_suite(source)


def test_environment_lock_requires_suite_order() -> None:
    suite = load_benchmark_environment_suite(SUITE_PATH)
    lock = seal_benchmark_environment_suite(suite, root=ROOT)
    reordered = lock.model_copy(update={"environments": list(reversed(lock.environments))})
    with pytest.raises(BenchmarkEnvironmentError, match="in order"):
        verify_benchmark_environment_suite(suite, reordered, root=ROOT)


def test_public_benchmark_cli_exposes_environment_commands() -> None:
    result = runner.invoke(benchmark_app, ["environments", "schema", "--kind", "suite"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["type"] == "object"
