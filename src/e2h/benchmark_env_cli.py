"""CLI for sealing, verifying, and materializing reproducible benchmark environments."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from e2h.benchmark_env import (
    BenchmarkEnvironmentError,
    BenchmarkEnvironmentSuite,
    BenchmarkEnvironmentSuiteLock,
    BenchmarkEnvironmentVerification,
    load_benchmark_environment_lock,
    load_benchmark_environment_suite,
    materialize_benchmark_environment,
    seal_benchmark_environment_suite,
    verify_benchmark_environment_suite,
)
from e2h.trace import write_json_atomic

environments_app = typer.Typer(
    no_args_is_help=True,
    help="Seal and verify reproducible coding, research, and browser environments.",
)
console = Console()
error_console = Console(stderr=True)


class EnvironmentSchemaKind(StrEnum):
    SUITE = "suite"
    LOCK = "lock"
    VERIFICATION = "verification"


def _load_suite(path: Path) -> BenchmarkEnvironmentSuite:
    try:
        return load_benchmark_environment_suite(path)
    except BenchmarkEnvironmentError as exc:
        error_console.print(f"[red]Invalid benchmark environment suite:[/red] {exc}")
        raise typer.Exit(code=2) from exc


def _load_lock(path: Path) -> BenchmarkEnvironmentSuiteLock:
    try:
        return load_benchmark_environment_lock(path)
    except BenchmarkEnvironmentError as exc:
        error_console.print(f"[red]Invalid benchmark environment lock:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@environments_app.command("seal")
def seal_environments(
    suite_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o", dir_okay=False)],
    root: Annotated[Path, typer.Option("--root", file_okay=False)] = Path("."),
) -> None:
    """Generate a lock binding the exact files of every environment source tree."""
    suite = _load_suite(suite_path)
    try:
        lock = seal_benchmark_environment_suite(suite, root=root)
    except BenchmarkEnvironmentError as exc:
        error_console.print(f"[red]Unable to seal benchmark environments:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    write_json_atomic(output, lock.model_dump_json(indent=2) + "\n")
    console.print(f"Wrote benchmark environment lock to {output}")


@environments_app.command("verify")
def verify_environments(
    suite_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    lock_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    root: Annotated[Path, typer.Option("--root", file_okay=False)] = Path("."),
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write verification as JSON."),
    ] = False,
) -> None:
    """Verify suite identity and every environment source tree against the lock."""
    suite = _load_suite(suite_path)
    lock = _load_lock(lock_path)
    try:
        verification = verify_benchmark_environment_suite(suite, lock, root=root)
    except BenchmarkEnvironmentError as exc:
        error_console.print(f"[red]Benchmark environment verification failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    rendered = verification.model_dump_json(indent=2) + "\n"
    if json_stdout:
        typer.echo(rendered, nl=False)
        return
    table = Table(title=f"E2H benchmark environments: {verification.suite_id}")
    table.add_column("Environments", justify="right")
    table.add_column("Files", justify="right")
    table.add_column("Bytes", justify="right")
    table.add_column("Suite SHA-256")
    table.add_row(
        str(verification.environment_count),
        str(verification.file_count),
        str(verification.total_bytes),
        verification.suite_sha256,
    )
    console.print(table)


@environments_app.command("materialize")
def materialize_environment(
    suite_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    lock_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    environment_id: Annotated[str, typer.Argument()],
    destination: Annotated[Path, typer.Argument()],
    root: Annotated[Path, typer.Option("--root", file_okay=False)] = Path("."),
) -> None:
    """Copy one verified source tree to a new destination without external downloads."""
    suite = _load_suite(suite_path)
    lock = _load_lock(lock_path)
    try:
        materialize_benchmark_environment(
            suite,
            lock,
            environment_id,
            root=root,
            destination=destination,
        )
    except BenchmarkEnvironmentError as exc:
        error_console.print(f"[red]Unable to materialize benchmark environment:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"Materialized {environment_id} to {destination}")


@environments_app.command("schema")
def write_environment_schema(
    kind: Annotated[EnvironmentSchemaKind, typer.Option("--kind")] = EnvironmentSchemaKind.SUITE,
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write one benchmark-environment JSON Schema."""
    if kind is EnvironmentSchemaKind.SUITE:
        schema = BenchmarkEnvironmentSuite.model_json_schema()
    elif kind is EnvironmentSchemaKind.LOCK:
        schema = BenchmarkEnvironmentSuiteLock.model_json_schema()
    else:
        schema = BenchmarkEnvironmentVerification.model_json_schema()
    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if output is None:
        typer.echo(rendered, nl=False)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote benchmark environment {kind.value} schema to {output}")
