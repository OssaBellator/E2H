"""Credential-free request planning for live provider runtime adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel
from rich.table import Table

from e2h.anthropic_runtime import (
    AnthropicRuntimeError,
    build_anthropic_messages_request,
    load_anthropic_messages_invocation,
)
from e2h.gemini_runtime import (
    GeminiRuntimeError,
    build_gemini_generate_content_request,
    load_gemini_generate_content_invocation,
)
from e2h.loader import CapsuleLoadError, load_capsule
from e2h.openai_runtime import (
    OpenAIRuntimeError,
    build_openai_responses_request,
    load_openai_responses_invocation,
)
from e2h.openai_runtime_cli import console, error_console
from e2h.trace import write_json_atomic
from e2h.variants import VariantError, load_variant_document

plan_app = typer.Typer(
    no_args_is_help=True,
    help="Materialize provider requests without credentials or network calls.",
)


def _emit_plan(
    request: BaseModel,
    *,
    noun: str,
    output: Path | None,
    json_stdout: bool,
) -> None:
    rendered = request.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json_atomic(output, rendered)
    if json_stdout:
        typer.echo(rendered, nl=False)
        return

    invocation_id = str(getattr(request, "invocation_id"))
    model = str(getattr(request, "model"))
    route_target_id = str(getattr(request, "route_target_id"))
    request_sha256 = str(getattr(request, "request_sha256"))
    table = Table(title=f"E2H {noun} request plan: {invocation_id}")
    table.add_column("Model")
    table.add_column("Route")
    table.add_column("Request digest")
    table.add_row(model, route_target_id, request_sha256)
    console.print(table)
    if output is not None:
        console.print(f"Wrote request plan to {output}")


def _fail_plan(noun: str, exc: Exception) -> None:
    error_console.print(f"[red]Unable to plan {noun} request:[/red] {exc}")
    raise typer.Exit(code=2) from exc


@plan_app.command("openai-responses")
def plan_openai_responses(
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    variant: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    invocation: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write the request plan as JSON to stdout."),
    ] = False,
) -> None:
    """Plan one verified OpenAI Responses request without calling OpenAI."""
    try:
        capsule_document = load_capsule(capsule)
        variant_document = load_variant_document(variant)
        invocation_document = load_openai_responses_invocation(invocation)
        request = build_openai_responses_request(
            variant_document,
            capsule_document,
            invocation_document,
        )
    except (CapsuleLoadError, VariantError, OpenAIRuntimeError) as exc:
        _fail_plan("OpenAI Responses", exc)
    _emit_plan(
        request,
        noun="OpenAI Responses",
        output=output,
        json_stdout=json_stdout,
    )


@plan_app.command("anthropic-messages")
def plan_anthropic_messages(
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    variant: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    invocation: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write the request plan as JSON to stdout."),
    ] = False,
) -> None:
    """Plan one verified Anthropic Messages request without calling Anthropic."""
    try:
        capsule_document = load_capsule(capsule)
        variant_document = load_variant_document(variant)
        invocation_document = load_anthropic_messages_invocation(invocation)
        request = build_anthropic_messages_request(
            variant_document,
            capsule_document,
            invocation_document,
        )
    except (CapsuleLoadError, VariantError, AnthropicRuntimeError) as exc:
        _fail_plan("Anthropic Messages", exc)
    _emit_plan(
        request,
        noun="Anthropic Messages",
        output=output,
        json_stdout=json_stdout,
    )


@plan_app.command("gemini-generate-content")
def plan_gemini_generate_content(
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    variant: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    invocation: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write the request plan as JSON to stdout."),
    ] = False,
) -> None:
    """Plan one verified Gemini GenerateContent request without calling Google."""
    try:
        capsule_document = load_capsule(capsule)
        variant_document = load_variant_document(variant)
        invocation_document = load_gemini_generate_content_invocation(invocation)
        request = build_gemini_generate_content_request(
            variant_document,
            capsule_document,
            invocation_document,
        )
    except (CapsuleLoadError, VariantError, GeminiRuntimeError) as exc:
        _fail_plan("Gemini GenerateContent", exc)
    _emit_plan(
        request,
        noun="Gemini GenerateContent",
        output=output,
        json_stdout=json_stdout,
    )


__all__ = ["plan_app"]
