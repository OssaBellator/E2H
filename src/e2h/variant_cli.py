"""Command-line interface for typed harness variant documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from e2h.loader import CapsuleLoadError, load_capsule
from e2h.trace import write_json_atomic
from e2h.variants import (
    HarnessVariantDocument,
    VariantError,
    load_variant_document,
    variant_document_sha256,
    verify_variant_document,
)

variant_app = typer.Typer(
    no_args_is_help=True,
    help="Validate content-addressed prompt, tool, context, routing, and workflow variants.",
)
console = Console()
error_console = Console(stderr=True)


@variant_app.command("validate")
def validate_variant_command(
    document: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write the verification document as JSON."),
    ] = False,
) -> None:
    """Verify one variant document against its exact base capsule."""
    try:
        loaded_document = load_variant_document(document)
        loaded_capsule = load_capsule(capsule)
        verification = verify_variant_document(loaded_document, loaded_capsule)
    except (VariantError, CapsuleLoadError) as exc:
        error_console.print(f"[red]Invalid variant:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rendered = verification.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json_atomic(output, rendered)
    if json_stdout:
        typer.echo(rendered, nl=False)
        return

    table = Table(title=f"E2H typed variant: {verification.variant_id}")
    table.add_column("Document SHA-256")
    table.add_column("Variant SHA-256")
    table.add_column("Dimensions")
    table.add_row(
        verification.document_sha256,
        verification.variant_sha256,
        ", ".join(verification.dimensions) or "environment-only",
    )
    console.print(table)


@variant_app.command("digest")
def digest_variant_command(
    document: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Print the canonical SHA-256 identity of a variant document."""
    try:
        loaded = load_variant_document(document)
    except VariantError as exc:
        error_console.print(f"[red]Invalid variant:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    typer.echo(variant_document_sha256(loaded))


@variant_app.command("schema")
def write_variant_schema(
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write the JSON Schema for bound typed variant documents."""
    rendered = (
        json.dumps(HarnessVariantDocument.model_json_schema(), indent=2, sort_keys=True) + "\n"
    )
    if output is None:
        console.print_json(rendered)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote variant schema to {output}")
