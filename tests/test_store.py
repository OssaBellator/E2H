from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from e2h.experiment import ExperimentResult, ExperimentRun, VariantSummary
from e2h.failures import summarize_failures, unexpected_exit_failure
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.store import (
    StoreError,
    export_parquet,
    ingest_artifact,
    initialize_store,
    query_store,
    store_info,
)
from e2h.store_models import ArtifactKind, QueryView

NOW = datetime(2026, 8, 6, tzinfo=UTC)


def command_result(
    check_id: str,
    *,
    status: CheckStatus = CheckStatus.PASSED,
    exit_code: int | None = 0,
    stdout: str = "ok\n",
    error: str | None = None,
) -> CommandResult:
    failure = (
        unexpected_exit_failure(exit_code, [0])
        if status is CheckStatus.FAILED and exit_code is not None
        else None
    )
    return CommandResult(
        id=check_id,
        argv=["python", "-c", "pass"],
        cwd=".",
        status=status,
        exit_code=exit_code,
        duration_seconds=0.1,
        stdout=stdout,
        stderr="",
        error=error,
        failure=failure,
    )


def run_result(
    capsule_id: str,
    *,
    status: RunStatus = RunStatus.PASSED,
    checks: list[CommandResult] | None = None,
    offset: int = 0,
) -> RunResult:
    started = NOW + timedelta(seconds=offset)
    resolved_checks = checks or [command_result("contract")]
    return RunResult(
        capsule_id=capsule_id,
        status=status,
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        duration_seconds=1,
        checks=resolved_checks,
        failure_summary=summarize_failures(
            (check.id, check.failure) for check in resolved_checks
        ),
    )


def write_run(path: Path, result: RunResult) -> None:
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")


def experiment_result() -> ExperimentResult:
    passed = run_result("capsule", offset=0)
    failed = run_result(
        "capsule",
        status=RunStatus.FAILED,
        checks=[
            command_result(
                "contract",
                status=CheckStatus.FAILED,
                exit_code=7,
                stdout="secret-output",
                error="contract failed",
            )
        ],
        offset=2,
    )
    return ExperimentResult(
        experiment_id="matrix",
        capsule_id="capsule",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=4),
        duration_seconds=4,
        runs=[
            ExperimentRun(
                run_id="matrix.baseline.000",
                variant_id="baseline",
                repetition=0,
                trace_id="trace-1",
                result=passed,
            ),
            ExperimentRun(
                run_id="matrix.profiled.000",
                variant_id="profiled",
                repetition=0,
                trace_id="trace-2",
                result=failed,
            ),
        ],
        summaries=[
            VariantSummary(
                variant_id="baseline",
                runs=1,
                passed=1,
                failed=0,
                errors=0,
                pass_rate=1,
                mean_duration_seconds=1,
            ),
            VariantSummary(
                variant_id="profiled",
                runs=1,
                passed=0,
                failed=1,
                errors=0,
                pass_rate=0,
                mean_duration_seconds=1,
            ),
        ],
    )


def test_standalone_run_ingestion_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "run.json"
    write_run(artifact, run_result("standalone"))

    first = ingest_artifact(database, artifact)
    second = ingest_artifact(database, artifact)

    assert first.inserted is True
    assert (first.runs, first.checks, first.summaries, first.failures) == (1, 1, 0, 0)
    assert second.inserted is False
    assert store_info(database).model_dump() == {
        "schema_version": "2",
        "sources": 1,
        "runs": 1,
        "checks": 1,
        "variant_summaries": 0,
        "failure_records": 0,
    }
    rows = query_store(database, QueryView.RUNS)
    assert rows[0]["capsule_id"] == "standalone"
    assert rows[0]["status"] == "passed"


def test_experiment_views_and_output_privacy(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "matrix.json"
    artifact.write_text(experiment_result().model_dump_json(indent=2), encoding="utf-8")

    result = ingest_artifact(database, artifact, kind=ArtifactKind.EXPERIMENT)
    assert (result.runs, result.checks, result.summaries, result.failures) == (2, 2, 2, 1)

    variants = query_store(database, QueryView.VARIANTS)
    assert [(row["variant_id"], row["pass_rate"]) for row in variants] == [
        ("baseline", 1.0),
        ("profiled", 0.0),
    ]
    failures = query_store(database, QueryView.FAILURES)
    assert len(failures) == 1
    assert failures[0]["check_id"] == "contract"
    assert failures[0]["exit_code"] == 7
    capsules = query_store(database, QueryView.CAPSULES)
    assert capsules[0]["runs"] == 2
    assert capsules[0]["pass_rate"] == 0.5

    checks = query_store(database, QueryView.CHECKS)
    failed = next(row for row in checks if row["status"] == "failed")
    assert failed["stdout_chars"] == len("secret-output")
    assert "secret-output" not in json.dumps(failed)
    with duckdb.connect(str(database), read_only=True) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info('checks')").fetchall()]
    assert "stdout" not in columns
    assert "stderr" not in columns


def test_duplicate_run_identity_rolls_back_before_insert(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "duplicate.json"
    payload = experiment_result().model_dump(mode="json")
    payload["runs"][1]["run_id"] = payload["runs"][0]["run_id"]
    artifact.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StoreError, match="duplicate run identity"):
        ingest_artifact(database, artifact)
    assert initialize_store(database).sources == 0
    assert store_info(database).runs == 0


def test_invalid_artifact_and_kind_are_rejected(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    bad = tmp_path / "bad.json"
    bad.write_text("not-json", encoding="utf-8")
    with pytest.raises(StoreError, match="valid UTF-8 JSON"):
        ingest_artifact(database, bad)

    run_path = tmp_path / "run.json"
    write_run(run_path, run_result("capsule"))
    with pytest.raises(StoreError, match="invalid experiment artifact"):
        ingest_artifact(database, run_path, kind=ArtifactKind.EXPERIMENT)


def test_query_limits_are_bounded(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    initialize_store(database)
    with pytest.raises(StoreError, match="between 1 and 10000"):
        query_store(database, QueryView.RUNS, limit=0)
    with pytest.raises(StoreError, match="between 1 and 10000"):
        query_store(database, QueryView.RUNS, limit=10_001)


def test_parquet_export_round_trips_through_duckdb(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "matrix.json"
    artifact.write_text(experiment_result().model_dump_json(indent=2), encoding="utf-8")
    ingest_artifact(database, artifact)

    output = tmp_path / "variants.parquet"
    assert export_parquet(database, output, QueryView.VARIANTS) == 2
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT variant_id, pass_rate FROM read_parquet(?) ORDER BY variant_id",
            [str(output)],
        ).fetchall()
    assert rows == [("baseline", 1.0), ("profiled", 0.0)]


def test_sources_store_only_basename(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    directory = tmp_path / "private" / "nested"
    directory.mkdir(parents=True)
    artifact = directory / "run.json"
    write_run(artifact, run_result("capsule"))
    ingest_artifact(database, artifact)
    source = query_store(database, QueryView.SOURCES)[0]
    assert source["source_name"] == "run.json"
    assert str(tmp_path) not in json.dumps(source)
