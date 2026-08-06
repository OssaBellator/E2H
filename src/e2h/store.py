"""Transactional DuckDB ingestion, query, and Parquet export."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from e2h.store_models import (
    MAX_QUERY_ROWS,
    SCHEMA_SQL,
    STORE_SCHEMA_VERSION,
    VIEW_SQL,
    ArtifactKind,
    IngestResult,
    QueryView,
    StoreInfo,
)
from e2h.store_rows import ArtifactError, normalize_rows, parse_artifact, read_artifact


class StoreError(RuntimeError):
    """Raised when an experiment store operation fails safely."""


def _connection(database: Path) -> duckdb.DuckDBPyConnection:
    database = database.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = duckdb.connect(str(database))
        connection.execute(SCHEMA_SQL)
        existing = connection.execute(
            "SELECT value FROM store_metadata WHERE key = 'schema_version'"
        ).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO store_metadata (key, value) VALUES ('schema_version', ?)",
                [STORE_SCHEMA_VERSION],
            )
        else:
            actual = str(existing[0])
            if actual == "1" and STORE_SCHEMA_VERSION == "2":
                connection.execute(
                    "UPDATE store_metadata SET value = ? WHERE key = 'schema_version'",
                    [STORE_SCHEMA_VERSION],
                )
            elif actual != STORE_SCHEMA_VERSION:
                connection.close()
                raise StoreError(
                    f"unsupported store schema version {actual!r}; expected {STORE_SCHEMA_VERSION}"
                )
        return connection
    except duckdb.Error as exc:
        raise StoreError(f"unable to open experiment store: {exc}") from exc


def _scalar_int(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = connection.execute(sql).fetchone()
    if row is None:
        raise StoreError("store count query returned no row")
    return int(row[0])


def _info(connection: duckdb.DuckDBPyConnection) -> StoreInfo:
    return StoreInfo(
        schema_version=STORE_SCHEMA_VERSION,
        sources=_scalar_int(connection, "SELECT count(*) FROM sources"),
        runs=_scalar_int(connection, "SELECT count(*) FROM runs"),
        checks=_scalar_int(connection, "SELECT count(*) FROM checks"),
        variant_summaries=_scalar_int(connection, "SELECT count(*) FROM variant_summaries"),
        failure_records=_scalar_int(connection, "SELECT count(*) FROM failure_records"),
    )


def initialize_store(database: Path) -> StoreInfo:
    """Create or validate a store and return its row counts."""
    connection = _connection(database)
    try:
        return _info(connection)
    finally:
        connection.close()


def store_info(database: Path) -> StoreInfo:
    """Return schema and row counts for an existing or new store."""
    return initialize_store(database)


def ingest_artifact(
    database: Path,
    artifact_path: Path,
    *,
    kind: ArtifactKind = ArtifactKind.AUTO,
) -> IngestResult:
    """Atomically ingest one standalone run or replay-matrix result."""
    try:
        selected_kind = ArtifactKind(kind)
        raw, payload = read_artifact(artifact_path)
        artifact_kind, artifact = parse_artifact(payload, selected_kind)
        source_sha256 = hashlib.sha256(raw).hexdigest()
        runs, checks, summaries, failures = normalize_rows(
            source_sha256,
            artifact_kind,
            artifact,
        )
    except (ArtifactError, ValueError) as exc:
        raise StoreError(str(exc)) from exc

    connection = _connection(database)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sources WHERE source_sha256 = ?",
            [source_sha256],
        ).fetchone()
        if exists is not None:
            return IngestResult(
                source_sha256=source_sha256,
                kind=artifact_kind,
                inserted=False,
                runs=0,
                checks=0,
                summaries=0,
                failures=0,
            )
        connection.execute("BEGIN TRANSACTION")
        try:
            schema_version = payload.get("schema_version")
            connection.execute(
                """
                INSERT INTO sources (
                    source_sha256, artifact_kind, source_name,
                    artifact_schema_version, ingested_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    source_sha256,
                    artifact_kind,
                    artifact_path.name,
                    str(schema_version) if schema_version is not None else None,
                    datetime.now(UTC),
                ],
            )
            if runs:
                connection.executemany(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    runs,
                )
            if checks:
                connection.executemany(
                    """
                    INSERT INTO checks VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    checks,
                )
            if failures:
                connection.executemany(
                    """
                    INSERT INTO failure_records VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    failures,
                )
            if summaries:
                connection.executemany(
                    """
                    INSERT INTO variant_summaries VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    summaries,
                )
            connection.execute("COMMIT")
        except duckdb.Error:
            connection.execute("ROLLBACK")
            raise
        return IngestResult(
            source_sha256=source_sha256,
            kind=artifact_kind,
            inserted=True,
            runs=len(runs),
            checks=len(checks),
            summaries=len(summaries),
            failures=len(failures),
        )
    except duckdb.Error as exc:
        raise StoreError(f"unable to ingest artifact: {exc}") from exc
    finally:
        connection.close()


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def query_store(
    database: Path,
    view: QueryView,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query one predefined analytical view with a bounded row limit."""
    if not 1 <= limit <= MAX_QUERY_ROWS:
        raise StoreError(f"limit must be between 1 and {MAX_QUERY_ROWS}")
    try:
        selected = QueryView(view)
    except ValueError as exc:
        raise StoreError(f"unknown query view: {view}") from exc
    connection = _connection(database)
    try:
        cursor = connection.execute(f"{VIEW_SQL[selected]} LIMIT {limit}")
        if cursor.description is None:
            return []
        columns = [str(item[0]) for item in cursor.description]
        return [
            {column: _normalize(value) for column, value in zip(columns, row, strict=True)}
            for row in cursor.fetchall()
        ]
    except duckdb.Error as exc:
        raise StoreError(f"unable to query store: {exc}") from exc
    finally:
        connection.close()


def export_parquet(
    database: Path,
    output: Path,
    view: QueryView,
    *,
    limit: int = MAX_QUERY_ROWS,
) -> int:
    """Export a bounded predefined view as Zstandard-compressed Parquet."""
    if not 1 <= limit <= MAX_QUERY_ROWS:
        raise StoreError(f"limit must be between 1 and {MAX_QUERY_ROWS}")
    try:
        selected = QueryView(view)
    except ValueError as exc:
        raise StoreError(f"unknown query view: {view}") from exc
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    escaped_output = str(output).replace("'", "''")
    sql = VIEW_SQL[selected]
    connection = _connection(database)
    try:
        count = _scalar_int(connection, f"SELECT count(*) FROM ({sql} LIMIT {limit})")
        connection.execute(
            f"COPY ({sql} LIMIT {limit}) TO '{escaped_output}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        return count
    except duckdb.Error as exc:
        raise StoreError(f"unable to export Parquet: {exc}") from exc
    finally:
        connection.close()
