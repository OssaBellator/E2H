"""Command-line interface for E2H."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from e2h.loader import CapsuleLoadError, load_capsule
from e2h.models import TaskCapsule
from e2h.runner import CheckStatus, RunStatus, RunnerError, run_capsule

app = typer.Typer(no_args_is_help=True, help="Evidence-to-Harness replay tools.")
console = Console()
error_console = Console(stderr=True)


@app.command()
def validate(capsule: Path = typer.Argument(..., exists=True, dir_okay=False)) -> None:
    """Validate a task capsule without executing it."""
    try:
        loaded = load_capsule(capsule)
    except CapsuleLoadError as exc:
        error_console.print(f"[red]Invalid capsule:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Valid[/green] {loaded.id} ({len(loaded.success.commands)} checks)")


@app.command("schema")
def write_schema(
    output: Path | None = typer.Option(None, "--output", "-o", dir_okay=False),
) -> None:
    """Print or write the JSON Schema for task capsules."""
    rendered = json.dumps(TaskCapsule.model_json_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        console.print_json(rendered)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    console.print(f"Wrote schema to {output}")


@app.command()
def run(
    capsule: Path = typer.Argument(..., exists=True, dir_okay=False),
    workspace: Path = typer.Option(Path("."), "--workspace", "-w", file_okay=False),
    output: Path | None = typer.Option(None, "--output", "-o", dir_okay=False),
    json_stdout: bool = typer.Option(False, "--json", help="Write the result as JSON."),
) -> None:
    """Execute a capsule's deterministic checks."""
    try:
        loaded = load_capsule(capsule)
        result = run_capsule(loaded, workspace)
    except (CapsuleLoadError, RunnerError) as exc:
        error_console.print(f"[red]Unable to run capsule:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rendered = result.model_dump_json(indent=2) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    if json_stdout:
        typer.echo(rendered, nl=False)
    else:
        table = Table(title=f"E2H replay: {result.capsule_id}")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Exit")
        table.add_column("Seconds", justify="right")
        for check in result.checks:
            style = "green" if check.status is CheckStatus.PASSED else "red"
            table.add_row(
                check.id,
                f"[{style}]{check.status.value}[/{style}]",
                "-" if check.exit_code is None else str(check.exit_code),
                f"{check.duration_seconds:.3f}",
            )
        console.print(table)
        console.print(f"Result: [bold]{result.status.value}[/bold]")

    if result.status is not RunStatus.PASSED:
        raise typer.Exit(code=1)
