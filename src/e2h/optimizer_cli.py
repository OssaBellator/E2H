"""Command-line interface for SDK-optional DSPy and GEPA adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from rich.console import Console
from rich.table import Table

from e2h.document import load_mapping_document
from e2h.loader import CapsuleLoadError, load_capsule
from e2h.optimizer_adapters import (
    DSPyDatasetDocument,
    OptimizerAdapterDocument,
    OptimizerAdapterError,
    OptimizerCandidateDocument,
    OptimizerFeedback,
    apply_optimizer_candidate,
    dspy_dataset_payload,
    feedback_from_run_result,
    gepa_prediction_payload,
    load_dspy_dataset,
    load_optimizer_adapter,
    load_optimizer_candidate,
    optimizer_candidate_sha256,
    verify_optimizer_adapter,
)
from e2h.runner import RunResult
from e2h.trace import write_json_atomic
from e2h.variants import VariantError, load_variant_document

optimizer_app = typer.Typer(
    no_args_is_help=True,
    help="Validate DSPy/GEPA datasets, feedback, and digest-bound prompt candidates.",
)
console = Console()
error_console = Console(stderr=True)


def _render_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


@optimizer_app.command("validate")
def validate_adapter_command(
    adapter: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    variant: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write the verification document as JSON."),
    ] = False,
) -> None:
    """Verify an adapter against its exact capsule and variant."""
    try:
        loaded_adapter = load_optimizer_adapter(adapter)
        loaded_capsule = load_capsule(capsule)
        loaded_variant = load_variant_document(variant)
        verification = verify_optimizer_adapter(
            loaded_adapter,
            loaded_capsule,
            loaded_variant,
        )
    except (OptimizerAdapterError, CapsuleLoadError, VariantError) as exc:
        error_console.print(f"[red]Invalid optimizer adapter:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    rendered = verification.model_dump_json(indent=2) + "\n"
    if output is not None:
        write_json_atomic(output, rendered)
    if json_stdout:
        typer.echo(rendered, nl=False)
        return
    table = Table(title=f"E2H optimizer adapter: {verification.adapter_id}")
    table.add_column("Optimizer")
    table.add_column("Adapter SHA-256")
    table.add_column("Components")
    table.add_row(
        verification.optimizer.value,
        verification.adapter_sha256,
        ", ".join(verification.component_ids),
    )
    console.print(table)


@optimizer_app.command("export-dataset")
def export_dataset_command(
    document: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Export values and input-field markers for ``dspy.Example``."""
    try:
        dataset = load_dspy_dataset(document)
    except OptimizerAdapterError as exc:
        error_console.print(f"[red]Invalid DSPy dataset:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    exported = [
        item.model_dump(mode="json") for item in dspy_dataset_payload(dataset)
    ]
    rendered = _render_json(exported)
    if output is None:
        typer.echo(rendered, nl=False)
        return
    write_json_atomic(output, rendered)
    console.print(f"Exported {len(dataset.examples)} DSPy examples to {output}")


@optimizer_app.command("apply")
def apply_candidate_command(
    adapter: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    candidate: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    variant: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o", dir_okay=False)],
) -> None:
    """Materialize one verified optimizer candidate as a bound variant document."""
    try:
        loaded_adapter = load_optimizer_adapter(adapter)
        loaded_candidate = load_optimizer_candidate(candidate)
        loaded_capsule = load_capsule(capsule)
        loaded_variant = load_variant_document(variant)
        result = apply_optimizer_candidate(
            loaded_adapter,
            loaded_candidate,
            loaded_capsule,
            loaded_variant,
        )
    except (OptimizerAdapterError, CapsuleLoadError, VariantError) as exc:
        error_console.print(f"[red]Invalid optimizer candidate:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    write_json_atomic(output, result.model_dump_json(indent=2) + "\n")
    console.print(
        f"Materialized {result.variant.id} from candidate "
        f"{optimizer_candidate_sha256(loaded_candidate)}"
    )


@optimizer_app.command("feedback")
def feedback_command(
    run: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    prediction_only: Annotated[
        bool,
        typer.Option(
            "--prediction",
            help="Emit only score and feedback kwargs for dspy.Prediction.",
        ),
    ] = False,
) -> None:
    """Convert one run result into sanitized optimizer feedback."""
    try:
        payload = load_mapping_document(
            run,
            noun="run result",
            max_bytes=2_097_152,
        )
        result = RunResult.model_validate(payload)
        feedback = feedback_from_run_result(result)
    except ValueError as exc:
        error_console.print(f"[red]Invalid run result:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    artifact: object = gepa_prediction_payload(feedback) if prediction_only else feedback
    rendered = _render_json(artifact)
    if output is None:
        typer.echo(rendered, nl=False)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote optimizer feedback to {output}")


@optimizer_app.command("schema")
def write_optimizer_schema(
    kind: Annotated[
        Literal["adapter", "candidate", "dataset", "feedback"],
        typer.Option("--kind"),
    ] = "adapter",
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write one optimizer adapter JSON Schema."""
    models = {
        "adapter": OptimizerAdapterDocument,
        "candidate": OptimizerCandidateDocument,
        "dataset": DSPyDatasetDocument,
        "feedback": OptimizerFeedback,
    }
    rendered = json.dumps(models[kind].model_json_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        console.print_json(rendered)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote {kind} schema to {output}")
