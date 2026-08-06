"""Command-line interface for typed harness genomes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from e2h.genome import (
    GenomeApplication,
    GenomeError,
    HarnessGenome,
    apply_genome,
    genome_sha256,
    load_genome,
    load_genome_application,
    materialize_application,
)
from e2h.loader import CapsuleLoadError, load_capsule
from e2h.models import TaskCapsule
from e2h.trace import write_json_atomic

genome_app = typer.Typer(no_args_is_help=True, help="Validate and apply typed harness genomes.")
console = Console()
error_console = Console(stderr=True)


def _write_capsule(path: Path, capsule: TaskCapsule) -> None:
    suffix = path.suffix.lower()
    if suffix == ".json":
        rendered = capsule.model_dump_json(indent=2) + "\n"
    elif suffix in {".yaml", ".yml"}:
        rendered = yaml.safe_dump(
            capsule.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        )
    else:
        raise GenomeError("materialized capsule must use .json, .yaml, or .yml")
    write_json_atomic(path, rendered)


def _emit_application(
    application: GenomeApplication,
    *,
    output: Path | None,
    json_stdout: bool,
) -> None:
    rendered = application.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json_atomic(output, rendered)
    if json_stdout:
        typer.echo(rendered, nl=False)
        return
    table = Table(title=f"E2H genome application: {application.genome_id}")
    table.add_column("Genome SHA-256")
    table.add_column("Base capsule")
    table.add_column("Result capsule")
    table.add_column("Patches", justify="right")
    table.add_row(
        application.genome_sha256,
        application.base_capsule_sha256,
        application.result_capsule_sha256,
        str(len(application.applied_patch_ids)),
    )
    console.print(table)


@genome_app.command("validate")
def validate_genome(
    genome: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate a genome and prove it applies cleanly to its bound capsule."""
    try:
        loaded_genome = load_genome(genome)
        loaded_capsule = load_capsule(capsule)
        application = apply_genome(loaded_genome, loaded_capsule)
    except (GenomeError, CapsuleLoadError) as exc:
        error_console.print(f"[red]Invalid genome:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        f"[green]Valid[/green] {loaded_genome.id} "
        f"({len(loaded_genome.patches)} patches, {application.result_capsule_sha256})"
    )


@genome_app.command("schema")
def write_genome_schema(
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write the JSON Schema for harness genomes."""
    rendered = json.dumps(HarnessGenome.model_json_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        console.print_json(rendered)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote genome schema to {output}")


@genome_app.command("apply")
def apply_genome_command(
    genome: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write the application document as JSON."),
    ] = False,
) -> None:
    """Apply a genome and emit a content-addressed application document."""
    try:
        loaded_genome = load_genome(genome)
        loaded_capsule = load_capsule(capsule)
        application = apply_genome(loaded_genome, loaded_capsule)
    except (GenomeError, CapsuleLoadError) as exc:
        error_console.print(f"[red]Unable to apply genome:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _emit_application(application, output=output, json_stdout=json_stdout)


@genome_app.command("materialize")
def materialize_genome_command(
    application: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o", dir_okay=False)],
) -> None:
    """Materialize a digest-verified genome application as a task capsule."""
    try:
        loaded = load_genome_application(application)
        capsule = materialize_application(loaded)
        _write_capsule(output, capsule)
    except GenomeError as exc:
        error_console.print(f"[red]Unable to materialize genome:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        f"Materialized {loaded.genome_id} ({genome_sha256(HarnessGenome.model_validate({
            'schema_version': '0.1',
            'id': loaded.genome_id,
            'base_capsule_sha256': loaded.base_capsule_sha256,
            'patches': [],
        })) if False else loaded.genome_sha256}) to {output}"
    )
