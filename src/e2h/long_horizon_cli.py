"""CLI for the long-horizon constraint retention/correction benchmark."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from e2h.long_horizon import (
    LongHorizonCorpus,
    LongHorizonError,
    LongHorizonEvaluationReport,
    LongHorizonPredictionDocument,
    PublicLongHorizonCorpus,
    evaluate_long_horizon_predictions,
    export_public_long_horizon_corpus,
    load_long_horizon_corpus,
    load_long_horizon_predictions,
    long_horizon_corpus_sha256,
    public_long_horizon_corpus_sha256,
)
from e2h.trace import write_json_atomic

long_horizon_app = typer.Typer(
    no_args_is_help=True,
    help="Validate, export, and score long-horizon constraint benchmarks.",
)
console = Console()
error_console = Console(stderr=True)


class LongHorizonSchemaKind(StrEnum):
    CORPUS = "corpus"
    PUBLIC = "public"
    PREDICTIONS = "predictions"
    REPORT = "report"


def _load_corpus(path: Path) -> LongHorizonCorpus:
    try:
        return load_long_horizon_corpus(path)
    except LongHorizonError as exc:
        error_console.print(f"[red]Invalid long-horizon corpus:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@long_horizon_app.command("validate")
def validate_long_horizon(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write validation metadata as JSON."),
    ] = False,
) -> None:
    """Validate correction chains and report private/public corpus identities."""
    corpus = _load_corpus(source)
    payload = {
        "schema_version": corpus.schema_version,
        "id": corpus.id,
        "tasks": len(corpus.tasks),
        "turns": sum(len(task.turns) for task in corpus.tasks),
        "probes": sum(len(task.probes) for task in corpus.tasks),
        "private_sha256": long_horizon_corpus_sha256(corpus),
        "public_sha256": public_long_horizon_corpus_sha256(corpus),
        "valid": True,
    }
    if json_stdout:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True) + "\n", nl=False)
        return
    console.print(
        f"[green]Valid[/green] {corpus.id}: {payload['tasks']} tasks, "
        f"{payload['turns']} turns, {payload['probes']} probes, "
        f"public_sha256={payload['public_sha256']}"
    )


@long_horizon_app.command("export")
def export_long_horizon(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o", dir_okay=False)],
) -> None:
    """Write the candidate-visible corpus with all evaluator-side updates removed."""
    corpus = _load_corpus(source)
    public = export_public_long_horizon_corpus(corpus)
    write_json_atomic(output, public.model_dump_json(indent=2) + "\n")
    console.print(
        f"Wrote label-free long-horizon corpus to {output} "
        f"({public_long_horizon_corpus_sha256(corpus)})"
    )


@long_horizon_app.command("evaluate")
def evaluate_long_horizon(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    predictions: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    require_complete: Annotated[
        bool,
        typer.Option(
            "--require-complete/--allow-partial",
            help="Fail when predictions omit any benchmark probe.",
        ),
    ] = True,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write evaluation report as JSON."),
    ] = False,
) -> None:
    """Score exact active-constraint maps without returning expected labels."""
    corpus = _load_corpus(source)
    try:
        prediction_document = load_long_horizon_predictions(predictions)
        report = evaluate_long_horizon_predictions(corpus, prediction_document)
    except LongHorizonError as exc:
        error_console.print(f"[red]Unable to evaluate long-horizon predictions:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    if require_complete and report.submitted != report.total:
        error_console.print(
            f"[red]Predictions cover {report.submitted}/{report.total} probes; "
            "complete coverage is required.[/red]"
        )
        raise typer.Exit(code=1)
    rendered = report.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json_atomic(output, rendered)
    if json_stdout:
        typer.echo(rendered, nl=False)
        return
    table = Table(title=f"E2H long-horizon benchmark: {report.corpus_id}")
    table.add_column("Submitted", justify="right")
    table.add_column("Correct", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Score", justify="right")
    table.add_row(
        str(report.submitted),
        str(report.correct),
        str(report.total),
        f"{report.score:.3f}",
    )
    console.print(table)


@long_horizon_app.command("schema")
def write_long_horizon_schema(
    kind: Annotated[LongHorizonSchemaKind, typer.Option("--kind")] = LongHorizonSchemaKind.CORPUS,
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write one long-horizon artifact JSON Schema."""
    if kind is LongHorizonSchemaKind.CORPUS:
        schema = LongHorizonCorpus.model_json_schema()
    elif kind is LongHorizonSchemaKind.PUBLIC:
        schema = PublicLongHorizonCorpus.model_json_schema()
    elif kind is LongHorizonSchemaKind.PREDICTIONS:
        schema = LongHorizonPredictionDocument.model_json_schema()
    else:
        schema = LongHorizonEvaluationReport.model_json_schema()
    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if output is None:
        typer.echo(rendered, nl=False)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote long-horizon {kind.value} schema to {output}")
