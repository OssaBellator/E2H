"""CLI commands for DuckDB experiment evidence storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from e2h.store import (
    StoreError,
    export_parquet,
    ingest_artifact,
    initialize_store,
    query_store,
    store_info,
)
from e2h.store_models import ArtifactKind, QueryView

store_app = typer.Typer(no_args_is_help=True, help="Store and query replay evidence.")
console = Console()
error_console = Console(stderr=True)


def _emit_rows(rows: list[dict[str, Any]], *, json_stdout: bool) -> None:
    if json_stdout:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        console.print("[yellow]No rows[/yellow]")
        return
    columns = list(rows[0])
    table = Table()
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row.get(column, "")) for column in columns))
    console.print(table)


@store_app.command("init")
def initialize_store_command(
    database: Annotated[Path, typer.Argument(dir_okay=False)],
    json_stdout: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create or validate a DuckDB experiment store."""
    try:
        info = initialize_store(database)
    except StoreError as exc:
        error_console.print(f"[red]Unable to initialize store:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    if json_stdout:
        typer.echo(info.model_dump_json(indent=2))
    else:
        console.print(
            f"[green]Ready[/green] schema {info.schema_version} "
            f"({info.sources} sources, {info.runs} runs, {info.failure_records} failures)"
        )


@store_app.command("ingest")
def ingest_store_command(
    database: Annotated[Path, typer.Argument(dir_okay=False)],
    artifacts: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False)],
    kind: Annotated[
        ArtifactKind,
        typer.Option("--kind", case_sensitive=False, help="Artifact type override."),
    ] = ArtifactKind.AUTO,
    json_stdout: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Transactionally ingest one or more run or matrix artifacts."""
    results = []
    try:
        for artifact in artifacts:
            results.append(ingest_artifact(database, artifact, kind=kind))
    except StoreError as exc:
        error_console.print(f"[red]Unable to ingest artifact:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    payload = [result.model_dump(mode="json") for result in results]
    if json_stdout:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    table = Table(title="E2H store ingestion")
    table.add_column("SHA-256")
    table.add_column("Kind")
    table.add_column("Inserted")
    table.add_column("Runs", justify="right")
    table.add_column("Checks", justify="right")
    table.add_column("Summaries", justify="right")
    table.add_column("Failures", justify="right")
    for result in results:
        table.add_row(
            result.source_sha256[:12],
            result.kind,
            "yes" if result.inserted else "no",
            str(result.runs),
            str(result.checks),
            str(result.summaries),
            str(result.failures),
        )
    console.print(table)


@store_app.command("query")
def query_store_command(
    database: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    view: Annotated[QueryView, typer.Argument(case_sensitive=False)],
    limit: Annotated[int, typer.Option(min=1, max=10_000)] = 100,
    json_stdout: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Query a stable analytical view without mutating the store."""
    try:
        rows = query_store(database, view, limit=limit, read_only=True)
    except StoreError as exc:
        error_console.print(f"[red]Unable to query store:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _emit_rows(rows, json_stdout=json_stdout)


@store_app.command("export")
def export_store_command(
    database: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Argument(dir_okay=False)],
    view: Annotated[
        QueryView,
        typer.Option("--view", case_sensitive=False, help="Analytical view to export."),
    ] = QueryView.RUNS,
    limit: Annotated[int, typer.Option(min=1, max=10_000)] = 10_000,
) -> None:
    """Export a stable analytical view as compressed Parquet."""
    try:
        rows = export_parquet(database, output, view, limit=limit)
    except StoreError as exc:
        error_console.print(f"[red]Unable to export store:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Exported[/green] {rows} rows to {output}")


@store_app.command("info")
def store_info_command(
    database: Annotated[Path, typer.Argument(dir_okay=False)],
    json_stdout: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show store schema and row counts."""
    try:
        info = store_info(database)
    except StoreError as exc:
        error_console.print(f"[red]Unable to inspect store:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    if json_stdout:
        typer.echo(info.model_dump_json(indent=2))
        return
    table = Table(title=f"E2H experiment store schema {info.schema_version}")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    table.add_row("sources", str(info.sources))
    table.add_row("runs", str(info.runs))
    table.add_row("checks", str(info.checks))
    table.add_row("variant_summaries", str(info.variant_summaries))
    table.add_row("failure_records", str(info.failure_records))
    console.print(table)
