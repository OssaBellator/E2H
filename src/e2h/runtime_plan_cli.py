"""CLI command for credential-free provider request planning."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from e2h.openai_runtime_cli import console, error_console
from e2h.runtime_plan import RuntimePlanError, RuntimeProvider, load_runtime_request_plan
from e2h.trace import write_json_atomic


def plan_runtime_request_command(
    provider: Annotated[
        RuntimeProvider,
        typer.Argument(help="Provider runtime to materialize without network I/O."),
    ],
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    variant: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    invocation: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False, help="Write request plan JSON."),
    ] = None,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write the complete request plan as JSON to stdout."),
    ] = False,
) -> None:
    """Materialize one exact provider request without credentials or network calls."""
    try:
        plan = load_runtime_request_plan(provider, capsule, variant, invocation)
    except RuntimePlanError as exc:
        error_console.print(f"[red]Unable to plan {provider.value} request:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rendered = plan.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json_atomic(output, rendered)
    if json_stdout:
        typer.echo(rendered, nl=False)
        return

    table = Table(title=f"E2H runtime request plan: {plan.request.invocation_id}")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Route")
    table.add_column("Request digest")
    table.add_row(
        plan.provider.value,
        plan.request.model,
        plan.request.route_target_id,
        plan.request_sha256,
    )
    console.print(table)
    if output is not None:
        console.print(f"Wrote request plan to {output}")


__all__ = ["plan_runtime_request_command"]
