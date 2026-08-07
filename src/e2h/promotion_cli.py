"""Command-line interface for statistical promotion gates and rollback metadata."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel
from rich.console import Console

from e2h.optimizer_adapters import OptimizerAdapterError, load_dspy_dataset
from e2h.partitions import DatasetPartitionError, load_dataset_partitions
from e2h.promotion import (
    PairedEvaluationReport,
    PromotionDecision,
    PromotionDecisionKind,
    PromotionError,
    PromotionGatePolicy,
    PromotionProposal,
    PromotionReceipt,
    RollbackEvent,
    RollbackPlan,
    VariantPredictionDocument,
    compare_variant_predictions,
    evaluate_promotion,
    load_paired_evaluation,
    load_promotion_decision,
    load_promotion_policy,
    load_promotion_proposal,
    load_promotion_receipt,
    load_rollback_plan,
    load_variant_predictions,
    materialize_promotion,
    paired_evaluation_sha256,
    promotion_decision_sha256,
    promotion_policy_sha256,
    promotion_proposal_sha256,
    promotion_receipt_sha256,
    record_rollback,
    rollback_plan_sha256,
    variant_prediction_sha256,
)
from e2h.trace import write_json_atomic

promotion_app = typer.Typer(
    no_args_is_help=True,
    help="Compare paired outcomes, enforce promotion gates, and record rollback metadata.",
)
console = Console()
error_console = Console(stderr=True)


def _write_or_echo(model: BaseModel, output: Path | None) -> None:
    rendered = model.model_dump_json(indent=2) + "\n"
    if output is None:
        typer.echo(rendered, nl=False)
        return
    write_json_atomic(output, rendered)


@promotion_app.command("compare")
def compare_command(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    dataset: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    baseline: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    candidate: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    evidence_id: Annotated[str, typer.Option("--evidence-id")],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Create aggregate paired evidence without exposing labels or case outcomes."""
    try:
        report = compare_variant_predictions(
            evidence_id,
            load_dataset_partitions(manifest),
            load_dspy_dataset(dataset),
            load_variant_predictions(baseline),
            load_variant_predictions(candidate),
        )
    except (PromotionError, DatasetPartitionError, OptimizerAdapterError) as exc:
        error_console.print(f"[red]Invalid promotion comparison:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _write_or_echo(report, output)


@promotion_app.command("evaluate")
def evaluate_command(
    policy: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    proposal: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    require_promotion: Annotated[
        bool,
        typer.Option("--require-promotion", help="Exit nonzero when the policy rejects."),
    ] = False,
) -> None:
    """Evaluate a candidate against every declared statistical gate."""
    try:
        decision = evaluate_promotion(
            load_promotion_policy(policy),
            load_promotion_proposal(proposal),
        )
    except PromotionError as exc:
        error_console.print(f"[red]Invalid promotion proposal:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _write_or_echo(decision, output)
    if require_promotion and decision.decision is not PromotionDecisionKind.PROMOTE:
        raise typer.Exit(code=1)


@promotion_app.command("materialize")
def materialize_command(
    decision: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    rollback: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Materialize a self-verifying passing decision with an exact rollback target."""
    try:
        receipt = materialize_promotion(
            load_promotion_decision(decision),
            load_rollback_plan(rollback),
        )
    except PromotionError as exc:
        error_console.print(f"[red]Invalid promotion materialization:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _write_or_echo(receipt, output)


@promotion_app.command("rollback")
def rollback_command(
    receipt: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    trigger_id: Annotated[str, typer.Option("--trigger")],
    observed_value: Annotated[float, typer.Option("--value")],
    observed_samples: Annotated[int, typer.Option("--samples", min=1)],
    event_id: Annotated[str, typer.Option("--event-id")],
    actor: Annotated[str, typer.Option("--actor")],
    occurred_at: Annotated[str, typer.Option("--occurred-at")],
    observed_window_seconds: Annotated[
        int | None,
        typer.Option("--window-seconds", min=1),
    ] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Record an auditable rollback event only when a declared trigger fires."""
    try:
        event = record_rollback(
            event_id,
            load_promotion_receipt(receipt),
            trigger_id,
            observed_value,
            observed_samples,
            actor,
            datetime.fromisoformat(occurred_at),
            observed_window_seconds=observed_window_seconds,
        )
    except ValueError as exc:
        error_console.print(f"[red]Invalid rollback event:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _write_or_echo(event, output)


@promotion_app.command("digest")
def digest_command(
    document: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    kind: Annotated[
        Literal[
            "predictions",
            "evidence",
            "policy",
            "proposal",
            "decision",
            "rollback",
            "receipt",
        ],
        typer.Option("--kind"),
    ],
) -> None:
    """Print the canonical SHA-256 identity of one promotion artifact."""
    try:
        if kind == "predictions":
            value = variant_prediction_sha256(load_variant_predictions(document))
        elif kind == "evidence":
            value = paired_evaluation_sha256(load_paired_evaluation(document))
        elif kind == "policy":
            value = promotion_policy_sha256(load_promotion_policy(document))
        elif kind == "proposal":
            value = promotion_proposal_sha256(load_promotion_proposal(document))
        elif kind == "decision":
            value = promotion_decision_sha256(load_promotion_decision(document))
        elif kind == "rollback":
            value = rollback_plan_sha256(load_rollback_plan(document))
        else:
            value = promotion_receipt_sha256(load_promotion_receipt(document))
        typer.echo(value)
    except PromotionError as exc:
        error_console.print(f"[red]Invalid promotion artifact:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@promotion_app.command("schema")
def schema_command(
    kind: Annotated[
        Literal[
            "predictions",
            "evidence",
            "policy",
            "proposal",
            "decision",
            "rollback",
            "receipt",
            "event",
        ],
        typer.Option("--kind"),
    ] = "policy",
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write one promotion and rollback JSON Schema."""
    models: dict[str, type[BaseModel]] = {
        "predictions": VariantPredictionDocument,
        "evidence": PairedEvaluationReport,
        "policy": PromotionGatePolicy,
        "proposal": PromotionProposal,
        "decision": PromotionDecision,
        "rollback": RollbackPlan,
        "receipt": PromotionReceipt,
        "event": RollbackEvent,
    }
    rendered = json.dumps(models[kind].model_json_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        console.print_json(rendered)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote {kind} schema to {output}")
