"""CLI commands for review-gated evidence-to-capsule compilation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from e2h.compiler import (
    CapsuleCompileError,
    ReviewDecision,
    compile_proposal,
    load_compiler_spec,
    load_ingestion_bundle,
    load_proposal,
    load_verification_report,
    materialize_capsule,
    review_proposal,
    verify_proposal,
)
from e2h.runner import RunnerError
from e2h.trace import write_json_atomic

compiler_app = typer.Typer(
    no_args_is_help=True,
    help="Compile sanitized evidence into review-gated task capsules.",
)
console = Console()
error_console = Console(stderr=True)


def _write_json(path: Path, payload: str) -> None:
    if path.suffix.lower() != ".json":
        raise CapsuleCompileError("structured compiler outputs must use .json")
    write_json_atomic(path, payload)


def _emit_json(
    payload: str,
    *,
    output: Path | None,
    json_stdout: bool,
) -> None:
    if output is not None:
        _write_json(output, payload)
    if json_stdout:
        typer.echo(payload, nl=False)


def _write_capsule(path: Path, capsule_payload: dict[str, object]) -> None:
    suffix = path.suffix.lower()
    if suffix == ".json":
        rendered = json.dumps(capsule_payload, indent=2, sort_keys=True) + "\n"
    elif suffix in {".yaml", ".yml"}:
        rendered = yaml.safe_dump(capsule_payload, sort_keys=False)
    else:
        raise CapsuleCompileError("materialized capsule must use .json, .yaml, or .yml")
    write_json_atomic(path, rendered)


@compiler_app.command("validate")
def validate_compiler_spec(
    specification: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate a compiler specification without producing a proposal."""
    try:
        spec = load_compiler_spec(specification)
    except CapsuleCompileError as exc:
        error_console.print(f"[red]Invalid compiler specification:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    generated_mutations = len(spec.oracles) if spec.auto_mutate_oracles else 0
    console.print(
        f"[green]Valid[/green] {spec.id} "
        f"({len(spec.checks) + len(spec.oracles)} checks, "
        f"{len(spec.mutations) + generated_mutations} mutations)"
    )


@compiler_app.command("proposal")
def compile_proposal_command(
    bundle: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    specification: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[
        bool, typer.Option("--json", help="Write the proposal as JSON.")
    ] = False,
) -> None:
    """Compile an ingestion bundle into an immutable draft proposal."""
    try:
        proposal = compile_proposal(
            load_ingestion_bundle(bundle),
            load_compiler_spec(specification),
        )
        rendered = proposal.model_dump_json(indent=2) + "\n"
        _emit_json(rendered, output=output, json_stdout=json_stdout)
    except CapsuleCompileError as exc:
        error_console.print(f"[red]Unable to compile proposal:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if not json_stdout:
        table = Table(title="E2H capsule proposal")
        table.add_column("Proposal")
        table.add_column("Checks", justify="right")
        table.add_column("Mutations", justify="right")
        table.add_column("Evidence", justify="right")
        table.add_row(
            proposal.proposal_id,
            str(len(proposal.core.capsule.success.commands)),
            str(len(proposal.core.mutations)),
            str(len(proposal.core.evidence)),
        )
        console.print(table)


@compiler_app.command("verify")
def verify_proposal_command(
    proposal_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    workspace: Annotated[Path, typer.Option("--workspace", "-w", file_okay=False)] = Path("."),
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the report as JSON.")] = False,
    require_strong: Annotated[
        bool,
        typer.Option("--require-strong", help="Return exit code 1 unless all gates pass."),
    ] = False,
) -> None:
    """Run the baseline capsule and every declared mutation probe."""
    try:
        report = verify_proposal(load_proposal(proposal_path), workspace)
        rendered = report.model_dump_json(indent=2) + "\n"
        _emit_json(rendered, output=output, json_stdout=json_stdout)
    except (CapsuleCompileError, RunnerError) as exc:
        error_console.print(f"[red]Unable to verify proposal:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    if not json_stdout:
        table = Table(title="E2H proposal verification")
        table.add_column("Baseline")
        table.add_column("Mutations", justify="right")
        table.add_column("Detected", justify="right")
        table.add_column("Strong")
        table.add_row(
            report.baseline.status.value,
            str(len(report.mutations)),
            str(sum(item.detected for item in report.mutations)),
            "yes" if report.strong else "no",
        )
        console.print(table)
    if require_strong and not report.strong:
        raise typer.Exit(code=1)


@compiler_app.command("review")
def review_proposal_command(
    proposal_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    reviewer: Annotated[str, typer.Option("--reviewer")],
    decision: Annotated[ReviewDecision, typer.Option("--decision", case_sensitive=False)],
    output: Annotated[Path, typer.Option("--output", "-o", dir_okay=False)],
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Append a human approval or rejection to a proposal."""
    try:
        proposal = review_proposal(
            load_proposal(proposal_path),
            reviewer=reviewer,
            decision=decision,
            note=note,
        )
        _write_json(output, proposal.model_dump_json(indent=2) + "\n")
    except (CapsuleCompileError, ValueError) as exc:
        error_console.print(f"[red]Unable to record review:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(
        f"Recorded [bold]{decision.value}[/bold] for {proposal.proposal_id} by {reviewer}"
    )


@compiler_app.command("materialize")
def materialize_capsule_command(
    proposal_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    verification_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o", dir_okay=False)],
    require_approved: Annotated[
        bool,
        typer.Option("--require-approved/--allow-unapproved"),
    ] = True,
    require_strong: Annotated[
        bool,
        typer.Option("--require-strong/--allow-weak"),
    ] = True,
) -> None:
    """Materialize an executable capsule after review and verification gates."""
    try:
        capsule = materialize_capsule(
            load_proposal(proposal_path),
            load_verification_report(verification_path),
            require_approved=require_approved,
            require_strong=require_strong,
        )
        _write_capsule(output, capsule.model_dump(mode="json"))
    except CapsuleCompileError as exc:
        error_console.print(f"[red]Unable to materialize capsule:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"Materialized [green]{capsule.id}[/green] to {output}")
