"""Transactional DuckDB ingestion, query, and Parquet export."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from e2h.store_export import ParquetOutputError, staged_parquet_output
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


def _validate_store_path(path: Path, *, noun: str) -> None:
    if "\x00" in str(path):
        raise StoreError(f"{noun} path must not contain NUL")


def _schema_version_row(connection: duckdb.DuckDBPyConnection) -> tuple[Any, ...] | None:
    rows = connection.execute(
        "SELECT value FROM store_metadata WHERE key = 'schema_version'"
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise StoreError("experiment store has multiple schema version markers")
    return rows[0]


def _connection(
    database: Path,
    *,
    read_only: bool = False,
) -> duckdb.DuckDBPyConnection:
    _validate_store_path(database, noun="store database")
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        database = database.resolve()
        if read_only:
            if not database.is_file():
                raise StoreError("experiment store does not exist")
            connection = duckdb.connect(str(database), read_only=True)
            existing = _schema_version_row(connection)
            if existing is None:
                connection.close()
                connection = None
                raise StoreError("experiment store schema version is missing")
            actual = str(existing[0])
            if actual != STORE_SCHEMA_VERSION:
                connection.close()
                connection = None
                raise StoreError(
                    f"unsupported store schema version {actual!r}; expected {STORE_SCHEMA_VERSION}"
                )
            return connection

        database.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect(str(database))
        connection.execute(SCHEMA_SQL)
        existing = _schema_version_row(connection)
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
                connection = None
                raise StoreError(
                    f"unsupported store schema version {actual!r}; expected {STORE_SCHEMA_VERSION}"
                )
        return connection
    except OSError as exc:
        if connection is not None:
            connection.close()
        raise StoreError(f"unable to prepare experiment store: {exc}") from exc
    except duckdb.Error as exc:
        if connection is not None:
            connection.close()
        raise StoreError(f"unable to open experiment store: {exc}") from exc


def _scalar_int(connection: duckdb.DuckDBPyConnection, sql: str) -> int:
    rows = connection.execute(sql).fetchall()
    if len(rows) != 1:
        raise StoreError("store count query did not return exactly one row")
    return int(rows[0][0])


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


def store_info(database: Path, *, read_only: bool = False) -> StoreInfo:
    """Return schema and row counts, optionally without mutating the store."""
    connection = _connection(database, read_only=read_only)
    try:
        return _info(connection)
    finally:
        connection.close()


def ingest_artifact(
    database: Path,
    artifact_path: Path,
    *,
    kind: ArtifactKind = ArtifactKind.AUTO,
) -> IngestResult:
    """Atomically ingest one standalone run or replay-matrix result."""
    _validate_store_path(artifact_path, noun="artifact")
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
        ).fetchall()
        if exists:
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
    read_only: bool = False,
) -> list[dict[str, Any]]:
    """Query one predefined analytical view with a bounded row limit."""
    if not 1 <= limit <= MAX_QUERY_ROWS:
        raise StoreError(f"limit must be between 1 and {MAX_QUERY_ROWS}")
    try:
        selected = QueryView(view)
    except ValueError as exc:
        raise StoreError(f"unknown query view: {view}") from exc
    connection = _connection(database, read_only=read_only)
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


def query_store_with_info(
    database: Path,
    view: QueryView,
    *,
    limit: int = 100,
    read_only: bool = False,
) -> tuple[StoreInfo, list[dict[str, Any]]]:
    """Return store metadata and one bounded view from the same transaction."""
    if not 1 <= limit <= MAX_QUERY_ROWS:
        raise StoreError(f"limit must be between 1 and {MAX_QUERY_ROWS}")
    try:
        selected = QueryView(view)
    except ValueError as exc:
        raise StoreError(f"unknown query view: {view}") from exc
    connection = _connection(database, read_only=read_only)
    try:
        connection.execute("BEGIN TRANSACTION")
        try:
            info = _info(connection)
            cursor = connection.execute(f"{VIEW_SQL[selected]} LIMIT {limit}")
            if cursor.description is None:
                rows: list[dict[str, Any]] = []
            else:
                columns = [str(item[0]) for item in cursor.description]
                rows = [
                    {
                        column: _normalize(value)
                        for column, value in zip(columns, row, strict=True)
                    }
                    for row in cursor.fetchall()
                ]
            connection.execute("COMMIT")
        except duckdb.Error:
            connection.execute("ROLLBACK")
            raise
        return info, rows
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
    _validate_store_path(output, noun="Parquet output")
    sql = VIEW_SQL[selected]
    connection = _connection(database)
    try:
        count = _scalar_int(connection, f"SELECT count(*) FROM ({sql} LIMIT {limit})")
        with staged_parquet_output(output) as staged:
            escaped_staged = str(staged).replace("'", "''")
            connection.execute(
                f"COPY ({sql} LIMIT {limit}) TO '{escaped_staged}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        return count
    except duckdb.Error as exc:
        raise StoreError(f"unable to export Parquet: {exc}") from exc
    except ParquetOutputError as exc:
        raise StoreError(str(exc)) from exc
    finally:
        connection.close()