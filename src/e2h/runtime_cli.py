"""Composed CLI for live provider runtime adapters."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from e2h.anthropic_messages import ingest_anthropic_messages_file
from e2h.anthropic_runtime import (
    AnthropicRuntimeError,
    load_anthropic_messages_invocation,
    run_anthropic_messages,
)
from e2h.ingest import EvidenceIngestError
from e2h.loader import CapsuleLoadError, load_capsule
from e2h.openai_runtime_cli import console, error_console, runtime_app
from e2h.privacy import RedactionPolicy, RedactionPolicyError, load_redaction_policy
from e2h.runtime_plan import RuntimePlanError, load_runtime_request_plan
from e2h.trace import write_json_atomic, write_traces_jsonl
from e2h.variants import VariantError, load_variant_document


@runtime_app.command("plan")
def plan_runtime_request_command(
    provider: Annotated[
        str,
        typer.Argument(
            help=(
                "Runtime provider: openai-responses, anthropic-messages, "
                "or gemini-generate-content."
            )
        ),
    ],
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    variant: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    invocation: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", dir_okay=False, help="Write the request plan as JSON."),
    ] = None,
    json_stdout: Annotated[
        bool, typer.Option("--json", help="Write the request plan as JSON.")
    ] = False,
) -> None:
    """Materialize one provider request without credentials or network I/O."""
    try:
        plan = load_runtime_request_plan(provider, capsule, variant, invocation)
    except RuntimePlanError as exc:
        error_console.print(f"[red]Unable to plan runtime request:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rendered = plan.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json_atomic(output, rendered)

    if json_stdout:
        typer.echo(rendered, nl=False)
        return

    table = Table(title=f"E2H runtime plan: {plan.request.invocation_id}")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Route")
    table.add_column("Request digest")
    table.add_row(
        plan.provider.value,
        plan.request.model,
        plan.request.route_target_id,
        plan.request.request_sha256,
    )
    console.print(table)
    console.print(f"Request digest: {plan.request.request_sha256}")
    if output is not None:
        console.print(f"Wrote request plan to {output}")


@runtime_app.command("anthropic-messages")
def run_anthropic_messages_command(
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    variant: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    invocation: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    archive: Annotated[Path, typer.Option("--archive", "-o", dir_okay=False)],
    result_output: Annotated[
        Path | None, typer.Option("--result", dir_okay=False, help="Write runtime audit JSON.")
    ] = None,
    bundle_output: Annotated[
        Path | None, typer.Option("--bundle", dir_okay=False, help="Write normalized evidence.")
    ] = None,
    traces: Annotated[Path | None, typer.Option("--traces", dir_okay=False)] = None,
    redaction_report: Annotated[
        Path | None, typer.Option("--redaction-report", dir_okay=False)
    ] = None,
    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,
    redaction_policy_path: Annotated[
        Path | None,
        typer.Option("--redaction-policy", exists=True, dir_okay=False),
    ] = None,
    api_key_env: Annotated[
        str,
        typer.Option(
            "--api-key-env",
            help="Environment variable containing the Anthropic API key.",
        ),
    ] = "ANTHROPIC_API_KEY",
    json_stdout: Annotated[
        bool, typer.Option("--json", help="Write the runtime audit result as JSON.")
    ] = False,
) -> None:
    """Execute one verified typed variant through the Anthropic Messages API."""
    try:
        if not api_key_env or "=" in api_key_env or "\x00" in api_key_env:
            raise AnthropicRuntimeError("api key environment variable name is invalid")
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise AnthropicRuntimeError(f"environment variable {api_key_env!r} is not set or empty")
        loaded_capsule = load_capsule(capsule)
        loaded_variant = load_variant_document(variant)
        loaded_invocation = load_anthropic_messages_invocation(invocation)
        runtime_result = run_anthropic_messages(
            loaded_variant,
            loaded_capsule,
            loaded_invocation,
            api_key=api_key,
        )
    except (CapsuleLoadError, VariantError, AnthropicRuntimeError) as exc:
        error_console.print(f"[red]Unable to run Anthropic Messages:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    archive_rendered = runtime_result.archive.model_dump_json(indent=2) + "\n"
    write_json_atomic(archive, archive_rendered)
    result_rendered = runtime_result.model_dump_json(indent=2) + "\n"
    if result_output is not None:
        write_json_atomic(result_output, result_rendered)

    if bundle_output is not None or traces is not None or redaction_report is not None:
        try:
            redaction_policy: RedactionPolicy | None = (
                load_redaction_policy(redaction_policy_path)
                if redaction_policy_path is not None
                else None
            )
            bundle = ingest_anthropic_messages_file(
                archive,
                redact=redact,
                redaction_policy=redaction_policy,
            )
        except (EvidenceIngestError, RedactionPolicyError) as exc:
            error_console.print(f"[red]Runtime archive ingestion failed:[/red] {exc}")
            raise typer.Exit(code=2) from exc
        if bundle_output is not None:
            write_json_atomic(bundle_output, bundle.model_dump_json(indent=2) + "\n")
        if traces is not None:
            write_traces_jsonl(traces, bundle.traces)
        if redaction_report is not None and bundle.redaction_review is not None:
            write_json_atomic(
                redaction_report,
                bundle.redaction_review.model_dump_json(indent=2) + "\n",
            )

    if json_stdout:
        typer.echo(result_rendered, nl=False)
    else:
        table = Table(title=f"E2H Anthropic runtime: {runtime_result.request.invocation_id}")
        table.add_column("Model")
        table.add_column("Route")
        table.add_column("Request digest")
        table.add_column("Policy")
        table.add_row(
            runtime_result.request.model,
            runtime_result.request.route_target_id,
            runtime_result.request.request_sha256,
            "accepted" if runtime_result.accepted else "violated",
        )
        console.print(table)
        console.print(f"Wrote raw Messages archive to {archive}")
        if runtime_result.policy_violations:
            for violation in runtime_result.policy_violations:
                error_console.print(f"[red]Policy violation:[/red] {violation}")

    if not runtime_result.accepted:
        raise typer.Exit(code=1)


__all__ = ["runtime_app"]
