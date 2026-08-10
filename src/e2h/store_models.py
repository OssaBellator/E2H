"""Typed models and stable SQL views for the E2H experiment store."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

STORE_SCHEMA_VERSION = "2"
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
MAX_QUERY_ROWS = 10_000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactKind(StrEnum):
    """Supported replay artifact types."""

    AUTO = "auto"
    RUN = "run"
    EXPERIMENT = "experiment"


class QueryView(StrEnum):
    """Stable, bounded analytical views exposed by the store."""

    RUNS = "runs"
    CHECKS = "checks"
    FAILURES = "failures"
    FAILURE_TAXONOMY = "failure-taxonomy"
    VARIANTS = "variants"
    CAPSULES = "capsules"
    SOURCES = "sources"


class IngestResult(StrictModel):
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["run", "experiment"]
    inserted: bool
    runs: int = Field(ge=0)
    checks: int = Field(ge=0)
    summaries: int = Field(ge=0)
    failures: int = Field(ge=0)

    @model_validator(mode="after")
    def row_counts_must_match_ingest_state(self) -> IngestResult:
        counts = (self.runs, self.checks, self.summaries, self.failures)
        if not self.inserted:
            if any(counts):
                raise ValueError("non-inserted ingests must report zero inserted rows")
            return self
        if self.runs < 1:
            raise ValueError("inserted ingests must contain at least one run")
        if self.failures > self.checks:
            raise ValueError("failure rows must not exceed check rows")
        if self.kind == "run":
            if self.runs != 1:
                raise ValueError("standalone run ingests must contain exactly one run")
            if self.summaries != 0:
                raise ValueError("standalone run ingests must not contain variant summaries")
        elif self.summaries < 1:
            raise ValueError("experiment ingests must contain at least one variant summary")
        elif self.summaries > self.runs:
            raise ValueError("experiment variant summaries must not exceed runs")
        return self


class StoreInfo(StrictModel):
    schema_version: str
    sources: int = Field(ge=0)
    runs: int = Field(ge=0)
    checks: int = Field(ge=0)
    variant_summaries: int = Field(ge=0)
    failure_records: int = Field(ge=0)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS store_metadata (
    key VARCHAR PRIMARY KEY,
    value VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    source_sha256 VARCHAR PRIMARY KEY,
    artifact_kind VARCHAR NOT NULL,
    source_name VARCHAR NOT NULL,
    artifact_schema_version VARCHAR,
    ingested_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_key VARCHAR PRIMARY KEY,
    source_sha256 VARCHAR NOT NULL,
    experiment_id VARCHAR,
    run_id VARCHAR,
    variant_id VARCHAR,
    repetition INTEGER,
    capsule_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    duration_seconds DOUBLE NOT NULL,
    check_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    check_key VARCHAR PRIMARY KEY,
    run_key VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    check_index INTEGER NOT NULL,
    check_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    exit_code INTEGER,
    duration_seconds DOUBLE NOT NULL,
    cwd VARCHAR NOT NULL,
    argv_json VARCHAR NOT NULL,
    stdout_chars INTEGER NOT NULL,
    stderr_chars INTEGER NOT NULL,
    stdout_sha256 VARCHAR NOT NULL,
    stderr_sha256 VARCHAR NOT NULL,
    stdout_truncated BOOLEAN NOT NULL,
    stderr_truncated BOOLEAN NOT NULL,
    error_message VARCHAR
);
CREATE TABLE IF NOT EXISTS failure_records (
    failure_key VARCHAR PRIMARY KEY,
    check_key VARCHAR NOT NULL,
    run_key VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL,
    category VARCHAR NOT NULL,
    code VARCHAR NOT NULL,
    impact VARCHAR NOT NULL,
    retryability VARCHAR NOT NULL,
    summary VARCHAR NOT NULL,
    details_json VARCHAR NOT NULL,
    caused_by_check_id VARCHAR,
    causes_json VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS variant_summaries (
    summary_key VARCHAR PRIMARY KEY,
    source_sha256 VARCHAR NOT NULL,
    experiment_id VARCHAR NOT NULL,
    capsule_id VARCHAR NOT NULL,
    variant_id VARCHAR NOT NULL,
    runs INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    failed INTEGER NOT NULL,
    errors INTEGER NOT NULL,
    pass_rate DOUBLE NOT NULL,
    mean_duration_seconds DOUBLE NOT NULL
);
"""


VIEW_SQL: dict[QueryView, str] = {
    QueryView.RUNS: """
        SELECT run_key, source_sha256, experiment_id, run_id, variant_id, repetition,
               capsule_id, status, started_at, finished_at, duration_seconds, check_count
        FROM runs
        ORDER BY started_at, run_key
    """,
    QueryView.CHECKS: """
        SELECT check_key, run_key, source_sha256, check_index, check_id, status, exit_code,
               duration_seconds, cwd, argv_json, stdout_chars, stderr_chars,
               stdout_sha256, stderr_sha256, stdout_truncated, stderr_truncated,
               error_message
        FROM checks
        ORDER BY run_key, check_index
    """,
    QueryView.FAILURES: """
        SELECT r.run_key, r.experiment_id, r.run_id, r.variant_id, r.repetition,
               r.capsule_id, r.status AS run_status, c.check_id,
               c.status AS check_status, c.exit_code, c.duration_seconds,
               c.error_message, f.category AS failure_category,
               f.code AS failure_code, f.impact AS failure_impact,
               f.retryability, f.summary AS failure_summary,
               f.details_json AS failure_details_json,
               f.caused_by_check_id, f.causes_json
        FROM runs AS r
        LEFT JOIN checks AS c ON c.run_key = r.run_key
        LEFT JOIN failure_records AS f ON f.check_key = c.check_key
        WHERE r.status <> 'passed' OR c.status <> 'passed'
        ORDER BY r.started_at, r.run_key, c.check_index
    """,
    QueryView.FAILURE_TAXONOMY: """
        SELECT f.category AS failure_category, f.code AS failure_code,
               f.impact AS failure_impact, f.retryability,
               count(*) AS occurrences,
               count(DISTINCT f.run_key) AS runs,
               count(DISTINCT r.capsule_id) AS capsules,
               avg(c.duration_seconds) AS mean_duration_seconds
        FROM failure_records AS f
        JOIN checks AS c ON c.check_key = f.check_key
        JOIN runs AS r ON r.run_key = f.run_key
        GROUP BY f.category, f.code, f.impact, f.retryability
        ORDER BY occurrences DESC, failure_category, failure_code
    """,
    QueryView.VARIANTS: """
        SELECT experiment_id, capsule_id, variant_id, runs, passed, failed, errors,
               pass_rate, mean_duration_seconds, source_sha256
        FROM variant_summaries
        ORDER BY experiment_id, variant_id
    """,
    QueryView.CAPSULES: """
        SELECT capsule_id,
               count(*) AS runs,
               sum(CASE WHEN status = 'passed' THEN 1 ELSE 0 END) AS passed,
               sum(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
               sum(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors,
               avg(CASE WHEN status = 'passed' THEN 1.0 ELSE 0.0 END) AS pass_rate,
               avg(duration_seconds) AS mean_duration_seconds,
               min(started_at) AS first_started_at,
               max(finished_at) AS last_finished_at
        FROM runs
        GROUP BY capsule_id
        ORDER BY capsule_id
    """,
    QueryView.SOURCES: """
        SELECT source_sha256, artifact_kind, source_name, artifact_schema_version, ingested_at
        FROM sources
        ORDER BY ingested_at, source_sha256
    """,
}
