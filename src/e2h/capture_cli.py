"""CLI for validating and inspecting first-party E2H capture documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from e2h.capture import (
    CaptureDocument,
    CaptureError,
    capture_document_sha256,
    load_capture_document,
)
from e2h.trace import write_json_atomic

capture_app = typer.Typer(no_args_is_help=True, help="Validate portable browser/VS Code captures.")
console = Console()
error_console = Console(stderr=True)


def _load(path: Path) -> CaptureDocument:
    try:
        return load_capture_document(path)
    except CaptureError as exc:
        error_console.print(f"[red]Invalid capture:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@capture_app.command("validate")
def validate_capture(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    json_stdout: Annotated[bool, typer.Option("--json", help="Write verification as JSON.")] = False,
) -> None:
    """Validate capture structure and every captured-content SHA-256."""
    document = _load(source)
    digest = capture_document_sha256(document)
    summary = {
        "schema_version": document.schema_version,
        "id": document.id,
        "client": document.client.value,
        "observations": len(document.observations),
        "sha256": digest,
        "valid": True,
    }
    if json_stdout:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True) + "\n", nl=False)
        return
    console.print(
        f"[green]Valid[/green] {document.id} "
        f"({document.client.value}, {len(document.observations)} observations, {digest})"
    )


@capture_app.command("inspect")
def inspect_capture(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    json_stdout: Annotated[bool, typer.Option("--json", help="Write metadata as JSON.")] = False,
) -> None:
    """Inspect capture metadata without printing captured content."""
    document = _load(source)
    payload = {
        "schema_version": document.schema_version,
        "id": document.id,
        "capsule_id": document.capsule_id,
        "client": document.client.value,
        "captured_at": document.captured_at.isoformat(),
        "observation_count": len(document.observations),
        "observation_kinds": [observation.kind.value for observation in document.observations],
        "content_sha256": [observation.content_sha256 for observation in document.observations],
        "sha256": capture_document_sha256(document),
    }
    if json_stdout:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True) + "\n", nl=False)
        return
    table = Table(title=f"E2H capture: {document.id}")
    table.add_column("Client")
    table.add_column("Observations", justify="right")
    table.add_column("Captured")
    table.add_column("Document SHA-256")
    table.add_row(
        document.client.value,
        str(len(document.observations)),
        document.captured_at.isoformat(),
        capture_document_sha256(document),
    )
    console.print(table)


@capture_app.command("schema")
def write_capture_schema(
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write the JSON Schema for portable capture documents."""
    rendered = json.dumps(CaptureDocument.model_json_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        typer.echo(rendered, nl=False)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote capture schema to {output}")
