"""CLI commands for deterministic workspace snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer
from rich.console import Console
from rich.table import Table

from e2h.snapshot import (
    DEFAULT_EXCLUDES,
    SnapshotError,
    SnapshotLimits,
    create_snapshot,
    restore_snapshot,
    snapshot_reference,
    verify_snapshot,
)

snapshot_app = typer.Typer(no_args_is_help=True, help="Create and verify workspace snapshots.")
console = Console()
error_console = Console(stderr=True)


@snapshot_app.command("create")
def create_snapshot_command(
    root: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    output: Annotated[Path, typer.Argument(dir_okay=False)],
    include: Annotated[list[str] | None, typer.Option("--include")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude")] = None,
    max_entries: Annotated[int, typer.Option(min=1)] = 10_000,
    max_file_bytes: Annotated[int, typer.Option(min=1)] = 100 * 1024 * 1024,
    max_total_bytes: Annotated[int, typer.Option(min=1)] = 1024 * 1024 * 1024,
) -> None:
    """Create a deterministic content-addressed snapshot archive."""
    try:
        manifest = create_snapshot(
            root,
            output,
            includes=include or ["."],
            excludes=exclude if exclude is not None else DEFAULT_EXCLUDES,
            limits=SnapshotLimits(
                max_entries=max_entries,
                max_file_bytes=max_file_bytes,
                max_total_bytes=max_total_bytes,
            ),
        )
    except SnapshotError as exc:
        error_console.print(f"[red]Unable to create snapshot:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        f"[green]Created[/green] {manifest.snapshot_id} "
        f"({len(manifest.core.entries)} entries, {manifest.core.total_bytes} bytes)"
    )


@snapshot_app.command("verify")
def verify_snapshot_command(
    archive: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Verify a snapshot manifest and every content blob."""
    try:
        manifest = verify_snapshot(archive)
    except SnapshotError as exc:
        error_console.print(f"[red]Invalid snapshot:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        f"[green]Valid[/green] {manifest.snapshot_id} "
        f"({len(manifest.core.entries)} entries, {manifest.core.total_bytes} bytes)"
    )


@snapshot_app.command("inspect")
def inspect_snapshot_command(
    archive: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    json_stdout: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a verified snapshot manifest."""
    try:
        manifest = verify_snapshot(archive)
    except SnapshotError as exc:
        error_console.print(f"[red]Invalid snapshot:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    if json_stdout:
        typer.echo(manifest.model_dump_json(indent=2))
        return
    table = Table(title=f"E2H snapshot {manifest.snapshot_id[:12]}")
    table.add_column("Path")
    table.add_column("Kind")
    table.add_column("Bytes", justify="right")
    for entry in manifest.core.entries:
        table.add_row(entry.path, entry.kind, str(entry.size_bytes))
    console.print(table)


@snapshot_app.command("restore")
def restore_snapshot_command(
    archive: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    destination: Annotated[Path, typer.Argument(file_okay=False)],
) -> None:
    """Verify and restore a snapshot into a new or empty directory."""
    try:
        manifest = restore_snapshot(archive, destination)
    except SnapshotError as exc:
        error_console.print(f"[red]Unable to restore snapshot:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Restored[/green] {manifest.snapshot_id} to {destination}")


@snapshot_app.command("reference")
def snapshot_reference_command(
    archive: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    locator: Annotated[str | None, typer.Option("--locator")] = None,
    role: Annotated[Literal["workspace", "artifact"], typer.Option("--role")] = "workspace",
) -> None:
    """Emit a verified snapshot reference as JSON."""
    try:
        reference = snapshot_reference(archive, locator=locator, role=role)
    except SnapshotError as exc:
        error_console.print(f"[red]Invalid snapshot:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(reference.model_dump(mode="json"), indent=2, sort_keys=True))
