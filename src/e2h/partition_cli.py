"""Command-line interface for content-addressed optimizer dataset partitions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from e2h.optimizer_adapters import OptimizerAdapterError, load_dspy_dataset
from e2h.partitions import (
    DatasetPartitionDocument,
    DatasetPartitionError,
    DatasetPartitionExport,
    DatasetPartitionVerification,
    PartitionRole,
    SealedEvaluationReport,
    SealedPredictionDocument,
    dataset_partition_sha256,
    evaluate_sealed_predictions,
    export_dataset_partition,
    load_dataset_partitions,
    load_sealed_predictions,
    verify_dataset_partitions,
)
from e2h.trace import write_json_atomic

partition_app = typer.Typer(
    no_args_is_help=True,
    help="Validate, export, and evaluate train, validation, and sealed-test splits.",
)
console = Console()
error_console = Console(stderr=True)


def _write_partition_output(path: Path, payload: str) -> None:
    try:
        write_json_atomic(path, payload)
    except OSError as exc:
        error_console.print(f"[red]Unable to write partition output:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@partition_app.command("validate")
def validate_partition_command(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    dataset: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write the verification document as JSON."),
    ] = False,
) -> None:
    """Verify one manifest against its exact optimizer dataset."""
    try:
        loaded_manifest = load_dataset_partitions(manifest)
        loaded_dataset = load_dspy_dataset(dataset)
        verification = verify_dataset_partitions(loaded_manifest, loaded_dataset)
    except (DatasetPartitionError, OptimizerAdapterError) as exc:
        error_console.print(f"[red]Invalid dataset partitions:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rendered = verification.model_dump_json(indent=2) + "\n"
    if output is not None:
        _write_partition_output(output, rendered)
    if json_stdout:
        typer.echo(rendered, nl=False)
        return

    table = Table(title=f"E2H dataset partitions: {verification.partition_id}")
    table.add_column("Public dataset SHA-256")
    table.add_column("Train")
    table.add_column("Validation")
    table.add_column("Sealed test")
    table.add_row(
        verification.public_dataset_sha256,
        str(verification.train_examples),
        str(verification.validation_examples),
        str(verification.sealed_test_examples),
    )
    console.print(table)


@partition_app.command("digest")
def digest_partition_command(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Print the private canonical SHA-256 identity of one partition manifest."""
    try:
        loaded = load_dataset_partitions(manifest)
    except DatasetPartitionError as exc:
        error_console.print(f"[red]Invalid dataset partitions:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    typer.echo(dataset_partition_sha256(loaded))


@partition_app.command("export")
def export_partition_command(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    dataset: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    role: Annotated[PartitionRole, typer.Option("--partition")],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Export one split, withholding labels for the sealed-test partition."""
    try:
        loaded_manifest = load_dataset_partitions(manifest)
        loaded_dataset = load_dspy_dataset(dataset)
        exported = export_dataset_partition(loaded_manifest, loaded_dataset, role)
    except (DatasetPartitionError, OptimizerAdapterError) as exc:
        error_console.print(f"[red]Invalid dataset partitions:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rendered = exported.model_dump_json(indent=2) + "\n"
    if output is None:
        typer.echo(rendered, nl=False)
        return
    _write_partition_output(output, rendered)
    console.print(f"Exported {len(exported.examples)} {role.value} examples to {output}")


@partition_app.command("evaluate")
def evaluate_partition_command(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    dataset: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    predictions: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Score sealed predictions without returning labels or per-example results."""
    try:
        loaded_manifest = load_dataset_partitions(manifest)
        loaded_dataset = load_dspy_dataset(dataset)
        loaded_predictions = load_sealed_predictions(predictions)
        report = evaluate_sealed_predictions(
            loaded_manifest,
            loaded_dataset,
            loaded_predictions,
        )
    except (DatasetPartitionError, OptimizerAdapterError) as exc:
        error_console.print(f"[red]Invalid sealed evaluation:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rendered = report.model_dump_json(indent=2) + "\n"
    if output is None:
        typer.echo(rendered, nl=False)
        return
    _write_partition_output(output, rendered)
    console.print(f"Sealed score: {report.correct}/{report.total} ({report.score:.3f})")


@partition_app.command("schema")
def write_partition_schema(
    kind: Annotated[
        Literal["manifest", "predictions", "export", "evaluation", "verification"],
        typer.Option("--kind"),
    ] = "manifest",
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write one dataset partition JSON Schema."""
    models: dict[str, type[BaseModel]] = {
        "manifest": DatasetPartitionDocument,
        "predictions": SealedPredictionDocument,
        "export": DatasetPartitionExport,
        "evaluation": SealedEvaluationReport,
        "verification": DatasetPartitionVerification,
    }
    rendered = json.dumps(models[kind].model_json_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        console.print_json(rendered)
        return
    _write_partition_output(output, rendered)
    console.print(f"Wrote {kind} schema to {output}")
