from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from e2h.failures import summarize_failures, unexpected_exit_failure
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.store import export_parquet, ingest_artifact, initialize_store, query_store, store_info
from e2h.store_models import QueryView

NOW = datetime(2026, 8, 6, tzinfo=UTC)


def failed_run() -> RunResult:
    failure = unexpected_exit_failure(7, [0])
    check = CommandResult(
        id="contract",
        argv=["python", "-c", "raise SystemExit(7)"],
        cwd=".",
        status=CheckStatus.FAILED,
        exit_code=7,
        duration_seconds=0.1,
        stdout="private-command-output",
        stderr="private-error-output",
        error=None,
        failure=failure,
    )
    return RunResult(
        capsule_id="taxonomy-capsule",
        status=RunStatus.FAILED,
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=1),
        duration_seconds=1,
        checks=[check],
        failure_summary=summarize_failures([("contract", failure)]),
    )


def test_failure_records_are_normalized_without_raw_output(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "run.json"
    artifact.write_text(failed_run().model_dump_json(indent=2), encoding="utf-8")

    result = ingest_artifact(database, artifact)
    assert result.failures == 1
    assert store_info(database).failure_records == 1

    failures = query_store(database, QueryView.FAILURES)
    assert len(failures) == 1
    row = failures[0]
    assert row["check_id"] == "contract"
    assert row["failure_category"] == "task"
    assert row["failure_code"] == "unexpected_exit"
    assert row["failure_impact"] == "evaluation_failure"
    assert json.loads(row["failure_details_json"]) == {
        "actual_exit_code": 7,
        "expected_exit_codes": [0],
    }
    assert "private-command-output" not in json.dumps(row)
    assert "private-error-output" not in json.dumps(row)

    with duckdb.connect(str(database), read_only=True) as connection:
        columns = [
            item[1]
            for item in connection.execute("PRAGMA table_info('failure_records')").fetchall()
        ]
        stored = connection.execute(
            "SELECT summary, details_json, causes_json FROM failure_records"
        ).fetchone()
    assert "stdout" not in columns
    assert "stderr" not in columns
    assert stored is not None
    assert "private-command-output" not in json.dumps(stored)
    assert "private-error-output" not in json.dumps(stored)


def test_failure_taxonomy_view_aggregates_stable_codes(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "run.json"
    artifact.write_text(failed_run().model_dump_json(indent=2), encoding="utf-8")
    ingest_artifact(database, artifact)

    rows = query_store(database, QueryView.FAILURE_TAXONOMY)
    assert rows == [
        {
            "failure_category": "task",
            "failure_code": "unexpected_exit",
            "failure_impact": "evaluation_failure",
            "retryability": "no",
            "occurrences": 1,
            "runs": 1,
            "capsules": 1,
            "mean_duration_seconds": 0.1,
        }
    ]


def test_failure_taxonomy_parquet_export_round_trips(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "run.json"
    artifact.write_text(failed_run().model_dump_json(indent=2), encoding="utf-8")
    ingest_artifact(database, artifact)

    output = tmp_path / "failure-taxonomy.parquet"
    assert export_parquet(database, output, QueryView.FAILURE_TAXONOMY) == 1
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT failure_code, occurrences FROM read_parquet(?)",
            [str(output)],
        ).fetchall()
    assert rows == [("unexpected_exit", 1)]


def test_identical_failure_artifact_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "evidence.duckdb"
    artifact = tmp_path / "run.json"
    artifact.write_text(failed_run().model_dump_json(indent=2), encoding="utf-8")
    first = ingest_artifact(database, artifact)
    second = ingest_artifact(database, artifact)
    assert first.failures == 1
    assert second.inserted is False
    assert second.failures == 0
    assert store_info(database).failure_records == 1


def test_schema_version_one_store_migrates_additively(tmp_path: Path) -> None:
    database = tmp_path / "legacy.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE store_metadata (key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL)"
        )
        connection.execute("INSERT INTO store_metadata VALUES ('schema_version', '1')")
    info = initialize_store(database)
    assert info.schema_version == "2"
    assert info.failure_records == 0
    with duckdb.connect(str(database), read_only=True) as connection:
        version = connection.execute(
            "SELECT value FROM store_metadata WHERE key = 'schema_version'"
        ).fetchone()
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
    assert version == ("2",)
    assert "failure_records" in tables
