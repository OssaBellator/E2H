"""Command-line interface for E2H."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from e2h.experiment import resolve_under_root
from e2h.experiment import run_experiment as execute_experiment
from e2h.loader import (
    CapsuleLoadError,
    ExperimentLoadError,
    load_capsule,
    load_experiment,
)
from e2h.models import TaskCapsule
from e2h.runner import CheckStatus, RunnerError, RunStatus, run_capsule
from e2h.trace import write_json_atomic, write_traces_jsonl

app = typer.Typer(no_args_is_help=True, help="Evidence-to-Harness replay tools.")
experiment_app = typer.Typer(no_args_is_help=True, help="Run reproducible replay matrices.")
app.add_typer(experiment_app, name="experiment")
console = Console()
error_console = Console(stderr=True)


@app.command()
def validate(
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate a task capsule without executing it."""
    try:
        loaded = load_capsule(capsule)
    except CapsuleLoadError as exc:
        error_console.print(f"[red]Invalid capsule:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Valid[/green] {loaded.id} ({len(loaded.success.commands)} checks)")


@app.command("schema")
def write_schema(
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write the JSON Schema for task capsules."""
    rendered = json.dumps(TaskCapsule.model_json_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        console.print_json(rendered)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote schema to {output}")


@app.command()
def run(
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    workspace: Annotated[Path, typer.Option("--workspace", "-w", file_okay=False)] = Path("."),
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the result as JSON.")] = False,
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
        write_json_atomic(output, rendered)
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


@experiment_app.command("validate")
def validate_experiment(
    specification: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate a replay-matrix specification without executing it."""
    try:
        loaded = load_experiment(specification)
    except ExperimentLoadError as exc:
        error_console.print(f"[red]Invalid experiment:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    total_runs = len(loaded.variants) * loaded.repetitions
    console.print(f"[green]Valid[/green] {loaded.id} ({total_runs} runs)")


@experiment_app.command("run")
def run_experiment_command(
    specification: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    root: Annotated[Path, typer.Option("--root", file_okay=False)] = Path("."),
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    traces: Annotated[Path | None, typer.Option("--traces", dir_okay=False)] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the result as JSON.")] = False,
    require_all_pass: Annotated[
        bool,
        typer.Option("--require-all-pass", help="Return exit code 1 when any matrix cell fails."),
    ] = False,
) -> None:
    """Execute every declared variant and repetition."""
    try:
        spec = load_experiment(specification)
        capsule_path = resolve_under_root(root, spec.capsule)
        workspace = resolve_under_root(root, spec.workspace)
        capsule = load_capsule(capsule_path)
        execution = execute_experiment(spec, capsule, workspace)
    except (RunnerError, ValueError) as exc:
        error_console.print(f"[red]Unable to run experiment:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rendered = execution.result.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json_atomic(output, rendered)
    if traces is not None:
        write_traces_jsonl(traces, execution.traces)

    if json_stdout:
        typer.echo(rendered, nl=False)
    else:
        table = Table(title=f"E2H experiment: {execution.result.experiment_id}")
        table.add_column("Variant")
        table.add_column("Passed", justify="right")
        table.add_column("Failed", justify="right")
        table.add_column("Errors", justify="right")
        table.add_column("Pass rate", justify="right")
        table.add_column("Mean seconds", justify="right")
        for summary in execution.result.summaries:
            table.add_row(
                summary.variant_id,
                str(summary.passed),
                str(summary.failed),
                str(summary.errors),
                f"{summary.pass_rate:.1%}",
                f"{summary.mean_duration_seconds:.3f}",
            )
        console.print(table)

    if require_all_pass and not execution.result.all_passed:
        raise typer.Exit(code=1)
