"""CLI for validating and inspecting E2H community benchmark corpora."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from e2h.benchmark import (
    BenchmarkError,
    FailurePatternCorpus,
    load_failure_pattern_corpus,
    verify_failure_pattern_corpus,
)
from e2h.benchmark_env_cli import environments_app
from e2h.long_horizon_cli import long_horizon_app
from e2h.trace import write_json_atomic

benchmark_app = typer.Typer(no_args_is_help=True, help="Validate E2H community benchmark corpora.")
benchmark_app.add_typer(environments_app, name="environments")
benchmark_app.add_typer(long_horizon_app, name="long-horizon")
console = Console()
error_console = Console(stderr=True)


def _write_benchmark_output(path: Path, payload: str) -> None:
    try:
        write_json_atomic(path, payload)
    except OSError as exc:
        error_console.print(f"[red]Unable to write benchmark output:[/red] {exc}")
        raise typer.Exit(code=2) from exc


def _load(path: Path) -> FailurePatternCorpus:
    try:
        return load_failure_pattern_corpus(path)
    except BenchmarkError as exc:
        error_console.print(f"[red]Invalid benchmark corpus:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@benchmark_app.command("validate")
def validate_benchmark(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    require_real_world: Annotated[
        bool,
        typer.Option(
            "--require-real-world/--allow-synthetic-only",
            help="Require at least one sanitized real-world pattern.",
        ),
    ] = True,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write benchmark verification as JSON."),
    ] = False,
) -> None:
    """Verify benchmark provenance, taxonomy consistency, and privacy residuals."""
    corpus = _load(source)
    verification = verify_failure_pattern_corpus(corpus)
    if require_real_world and verification.sanitized_real_world_count == 0:
        error_console.print("[red]Benchmark contains no sanitized real-world patterns.[/red]")
        raise typer.Exit(code=1)
    if not verification.verified:
        error_console.print(
            f"[red]Benchmark privacy verification found "
            f"{verification.privacy_findings} residual sensitive patterns.[/red]"
        )
        raise typer.Exit(code=1)
    rendered = verification.model_dump_json(indent=2) + "\n"
    if json_stdout:
        typer.echo(rendered, nl=False)
        return
    console.print(
        f"[green]Verified[/green] {verification.corpus_id}: "
        f"{verification.pattern_count} patterns, "
        f"{verification.sanitized_real_world_count} sanitized real-world, "
        f"sha256={verification.corpus_sha256}"
    )


@benchmark_app.command("inspect")
def inspect_benchmark(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write metadata as JSON."),
    ] = False,
) -> None:
    """Inspect taxonomy/provenance counts without reproducing source issue text."""
    corpus = _load(source)
    verification = verify_failure_pattern_corpus(corpus)
    if json_stdout:
        typer.echo(verification.model_dump_json(indent=2) + "\n", nl=False)
        return
    table = Table(title=f"E2H benchmark: {verification.corpus_id}")
    table.add_column("Patterns", justify="right")
    table.add_column("Real-world", justify="right")
    table.add_column("Synthetic", justify="right")
    table.add_column("Privacy findings", justify="right")
    table.add_column("Corpus SHA-256")
    table.add_row(
        str(verification.pattern_count),
        str(verification.sanitized_real_world_count),
        str(verification.synthetic_count),
        str(verification.privacy_findings),
        verification.corpus_sha256,
    )
    console.print(table)


@benchmark_app.command("schema")
def write_benchmark_schema(
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write the failure-pattern corpus JSON Schema."""
    rendered = json.dumps(FailurePatternCorpus.model_json_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        typer.echo(rendered, nl=False)
        return
    _write_benchmark_output(output, rendered)
    console.print(f"Wrote benchmark schema to {output}")
