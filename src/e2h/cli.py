"""Command-line interface for E2H."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from e2h.anthropic_messages import ingest_anthropic_messages_file
from e2h.compiler_cli import compiler_app
from e2h.experiment import resolve_under_root
from e2h.experiment import run_experiment as execute_experiment
from e2h.gemini_generate_content import ingest_gemini_generate_content_file
from e2h.ingest import (
    EvidenceIngestError,
    IngestionBundle,
    ingest_otlp_file,
    ingest_transcript_file,
)
from e2h.loader import (
    CapsuleLoadError,
    ExperimentLoadError,
    load_capsule,
    load_experiment,
)
from e2h.models import TaskCapsule
from e2h.openai_responses import ingest_openai_responses_file
from e2h.privacy import (
    RedactionPolicy,
    RedactionPolicyError,
    load_redaction_policy,
)
from e2h.runner import (
    CheckStatus,
    ExecutionBackend,
    RunnerError,
    RunStatus,
    run_capsule,
)
from e2h.snapshot_cli import snapshot_app
from e2h.store_cli import store_app
from e2h.trace import write_json_atomic, write_traces_jsonl

app = typer.Typer(no_args_is_help=True, help="Evidence-to-Harness replay tools.")
experiment_app = typer.Typer(no_args_is_help=True, help="Run reproducible replay matrices.")
ingest_app = typer.Typer(no_args_is_help=True, help="Normalize observable evidence.")
app.add_typer(compiler_app, name="compile")
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(store_app, name="store")
app.add_typer(experiment_app, name="experiment")
app.add_typer(ingest_app, name="ingest")
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
    backend: Annotated[
        ExecutionBackend,
        typer.Option("--backend", case_sensitive=False, help="Execution backend."),
    ] = ExecutionBackend.AUTO,
    container_runtime: Annotated[
        str | None,
        typer.Option("--container-runtime", help="Trusted Docker-compatible runtime binary."),
    ] = None,
) -> None:
    """Execute a capsule's deterministic checks."""
    try:
        loaded = load_capsule(capsule)
        result = run_capsule(
            loaded,
            workspace,
            backend=backend,
            container_runtime=container_runtime,
        )
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
    backend: Annotated[
        ExecutionBackend,
        typer.Option("--backend", case_sensitive=False, help="Execution backend."),
    ] = ExecutionBackend.AUTO,
    container_runtime: Annotated[
        str | None,
        typer.Option("--container-runtime", help="Trusted Docker-compatible runtime binary."),
    ] = None,
) -> None:
    """Execute every declared variant and repetition."""
    try:
        spec = load_experiment(specification)
        capsule_path = resolve_under_root(root, spec.capsule)
        workspace = resolve_under_root(root, spec.workspace)
        capsule = load_capsule(capsule_path)
        execution = execute_experiment(
            spec,
            capsule,
            workspace,
            backend=backend,
            container_runtime=container_runtime,
        )
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


def _emit_ingestion(
    bundle: IngestionBundle,
    *,
    output: Path | None,
    traces: Path | None,
    redaction_report: Path | None,
    json_stdout: bool,
) -> None:
    rendered = bundle.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json_atomic(output, rendered)
    if traces is not None:
        write_traces_jsonl(traces, bundle.traces)
    if redaction_report is not None and bundle.redaction_review is not None:
        write_json_atomic(
            redaction_report,
            bundle.redaction_review.model_dump_json(indent=2) + "\n",
        )
    if json_stdout:
        typer.echo(rendered, nl=False)
        return

    event_count = sum(len(trace.events) for trace in bundle.traces)
    table = Table(title="E2H evidence ingestion")
    table.add_column("Source")
    table.add_column("Traces", justify="right")
    table.add_column("Events", justify="right")
    table.add_column("Corrections", justify="right")
    table.add_column("Redactions", justify="right")
    table.add_row(
        bundle.provenance.source_name,
        str(len(bundle.traces)),
        str(event_count),
        str(len(bundle.corrections)),
        str(len(bundle.redactions)),
    )
    console.print(table)
    review = bundle.redaction_review
    privacy_table = Table(title="E2H privacy review")
    privacy_table.add_column("Policy")
    privacy_table.add_column("Residuals", justify="right")
    privacy_table.add_row(
        review.policy_id if review is not None else "-",
        str(len(review.residual_findings) if review is not None else 0),
    )
    console.print(privacy_table)


@ingest_app.command("transcript")
def ingest_transcript_command(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule_id: Annotated[str | None, typer.Option("--capsule-id")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    traces: Annotated[Path | None, typer.Option("--traces", dir_okay=False)] = None,
    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,
    redaction_policy_path: Annotated[
        Path | None,
        typer.Option("--redaction-policy", exists=True, dir_okay=False),
    ] = None,
    redaction_report: Annotated[
        Path | None, typer.Option("--redaction-report", dir_okay=False)
    ] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the bundle as JSON.")] = False,
) -> None:
    """Import a canonical transcript JSON document."""
    try:
        redaction_policy: RedactionPolicy | None = (
            load_redaction_policy(redaction_policy_path)
            if redaction_policy_path is not None
            else None
        )
        bundle = ingest_transcript_file(
            source,
            capsule_id=capsule_id,
            redact=redact,
            redaction_policy=redaction_policy,
        )
    except (EvidenceIngestError, RedactionPolicyError) as exc:
        error_console.print(f"[red]Unable to ingest evidence:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _emit_ingestion(
        bundle,
        output=output,
        traces=traces,
        redaction_report=redaction_report,
        json_stdout=json_stdout,
    )


@ingest_app.command("openai-responses")
def ingest_openai_responses_command(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule_id: Annotated[str | None, typer.Option("--capsule-id")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    traces: Annotated[Path | None, typer.Option("--traces", dir_okay=False)] = None,
    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,
    redaction_policy_path: Annotated[
        Path | None,
        typer.Option("--redaction-policy", exists=True, dir_okay=False),
    ] = None,
    redaction_report: Annotated[
        Path | None, typer.Option("--redaction-report", dir_okay=False)
    ] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the bundle as JSON.")] = False,
) -> None:
    """Import an archived OpenAI Responses API export."""
    try:
        redaction_policy: RedactionPolicy | None = (
            load_redaction_policy(redaction_policy_path)
            if redaction_policy_path is not None
            else None
        )
        bundle = ingest_openai_responses_file(
            source,
            capsule_id=capsule_id,
            redact=redact,
            redaction_policy=redaction_policy,
        )
    except (EvidenceIngestError, RedactionPolicyError) as exc:
        error_console.print(f"[red]Unable to ingest evidence:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _emit_ingestion(
        bundle,
        output=output,
        traces=traces,
        redaction_report=redaction_report,
        json_stdout=json_stdout,
    )


@ingest_app.command("anthropic-messages")
def ingest_anthropic_messages_command(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule_id: Annotated[str | None, typer.Option("--capsule-id")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    traces: Annotated[Path | None, typer.Option("--traces", dir_okay=False)] = None,
    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,
    redaction_policy_path: Annotated[
        Path | None,
        typer.Option("--redaction-policy", exists=True, dir_okay=False),
    ] = None,
    redaction_report: Annotated[
        Path | None, typer.Option("--redaction-report", dir_okay=False)
    ] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the bundle as JSON.")] = False,
) -> None:
    """Import an archived Anthropic Messages API export."""
    try:
        redaction_policy: RedactionPolicy | None = (
            load_redaction_policy(redaction_policy_path)
            if redaction_policy_path is not None
            else None
        )
        bundle = ingest_anthropic_messages_file(
            source,
            capsule_id=capsule_id,
            redact=redact,
            redaction_policy=redaction_policy,
        )
    except (EvidenceIngestError, RedactionPolicyError) as exc:
        error_console.print(f"[red]Unable to ingest evidence:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _emit_ingestion(
        bundle,
        output=output,
        traces=traces,
        redaction_report=redaction_report,
        json_stdout=json_stdout,
    )


@ingest_app.command("gemini-generate-content")
def ingest_gemini_generate_content_command(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule_id: Annotated[str | None, typer.Option("--capsule-id")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    traces: Annotated[Path | None, typer.Option("--traces", dir_okay=False)] = None,
    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,
    redaction_policy_path: Annotated[
        Path | None,
        typer.Option("--redaction-policy", exists=True, dir_okay=False),
    ] = None,
    redaction_report: Annotated[
        Path | None, typer.Option("--redaction-report", dir_okay=False)
    ] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the bundle as JSON.")] = False,
) -> None:
    """Import an archived Gemini GenerateContent API export."""
    try:
        redaction_policy: RedactionPolicy | None = (
            load_redaction_policy(redaction_policy_path)
            if redaction_policy_path is not None
            else None
        )
        bundle = ingest_gemini_generate_content_file(
            source,
            capsule_id=capsule_id,
            redact=redact,
            redaction_policy=redaction_policy,
        )
    except (EvidenceIngestError, RedactionPolicyError) as exc:
        error_console.print(f"[red]Unable to ingest evidence:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _emit_ingestion(
        bundle,
        output=output,
        traces=traces,
        redaction_report=redaction_report,
        json_stdout=json_stdout,
    )


@ingest_app.command("otlp")
def ingest_otlp_command(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule_id: Annotated[str, typer.Option("--capsule-id")] = "unassigned",
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    traces: Annotated[Path | None, typer.Option("--traces", dir_okay=False)] = None,
    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,
    redaction_policy_path: Annotated[
        Path | None,
        typer.Option("--redaction-policy", exists=True, dir_okay=False),
    ] = None,
    redaction_report: Annotated[
        Path | None, typer.Option("--redaction-report", dir_okay=False)
    ] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the bundle as JSON.")] = False,
) -> None:
    """Import an OTLP/HTTP JSON trace export."""
    try:
        redaction_policy: RedactionPolicy | None = (
            load_redaction_policy(redaction_policy_path)
            if redaction_policy_path is not None
            else None
        )
        bundle = ingest_otlp_file(
            source,
            capsule_id=capsule_id,
            redact=redact,
            redaction_policy=redaction_policy,
        )
    except (EvidenceIngestError, RedactionPolicyError) as exc:
        error_console.print(f"[red]Unable to ingest evidence:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _emit_ingestion(
        bundle,
        output=output,
        traces=traces,
        redaction_report=redaction_report,
        json_stdout=json_stdout,
    )
