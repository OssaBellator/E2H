from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from e2h.optimizer_adapters import DSPyDatasetDocument
from e2h.partitions import (
    DatasetPartitionDocument,
    dataset_partition_public_sha256,
    dspy_dataset_public_sha256,
    dspy_dataset_sha256,
)
from e2h.promotion import (
    PairedEvaluationReport,
    PromotionDecision,
    PromotionGatePolicy,
    PromotionProposal,
    RollbackPlan,
    VariantPredictionDocument,
    promotion_decision_sha256,
)
from e2h.promotion_cli import promotion_app

runner = CliRunner()
BASE_SHA = "1" * 64
CANDIDATE_SHA = "2" * 64


def _write(path: Path, value: object) -> None:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _files(tmp_path: Path) -> dict[str, Path]:
    examples = [
        {"id": "train", "inputs": {"task": "train"}, "outputs": {"answer": "ok"}},
        {"id": "sealed", "inputs": {"task": "sealed"}, "outputs": {"answer": "ok"}},
    ]
    for index in range(6):
        examples.append(
            {
                "id": f"validation-{index}",
                "inputs": {"task": f"validation {index}"},
                "outputs": {"answer": "ok"},
            }
        )
    dataset = DSPyDatasetDocument.model_validate({"id": "cli", "examples": examples})
    manifest = DatasetPartitionDocument(
        id="cli-split",
        dataset_sha256=dspy_dataset_sha256(dataset),
        public_dataset_sha256=dspy_dataset_public_sha256(dataset),
        train=["train"],
        validation=[f"validation-{index}" for index in range(6)],
        sealed_test=["sealed"],
    )
    common = {
        "public_dataset_sha256": manifest.public_dataset_sha256,
        "public_partition_sha256": dataset_partition_public_sha256(manifest),
        "role": "validation",
    }
    baseline = VariantPredictionDocument.model_validate(
        {
            **common,
            "variant_id": "baseline",
            "variant_sha256": BASE_SHA,
            "predictions": [
                {"example_id": f"validation-{index}", "outputs": {"answer": "wrong"}}
                for index in range(6)
            ],
        }
    )
    candidate = VariantPredictionDocument.model_validate(
        {
            **common,
            "variant_id": "candidate",
            "variant_sha256": CANDIDATE_SHA,
            "predictions": [
                {
                    "example_id": f"validation-{index}",
                    "outputs": {"answer": "ok" if index < 5 else "wrong"},
                }
                for index in range(6)
            ],
        }
    )
    paths = {
        "dataset": tmp_path / "dataset.yaml",
        "manifest": tmp_path / "manifest.yaml",
        "baseline": tmp_path / "baseline.yaml",
        "candidate": tmp_path / "candidate.yaml",
    }
    _write(paths["dataset"], dataset)
    _write(paths["manifest"], manifest)
    _write(paths["baseline"], baseline)
    _write(paths["candidate"], candidate)
    return paths


def test_cli_compare_evaluate_materialize_and_rollback(tmp_path: Path) -> None:
    paths = _files(tmp_path)
    evidence_path = tmp_path / "evidence.json"
    result = runner.invoke(
        promotion_app,
        [
            "compare",
            str(paths["manifest"]),
            str(paths["dataset"]),
            str(paths["baseline"]),
            str(paths["candidate"]),
            "--evidence-id",
            "cli-evidence",
            "--output",
            str(evidence_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    evidence = PairedEvaluationReport.model_validate(json.loads(evidence_path.read_text()))
    assert evidence.candidate_only_correct == 5

    policy = PromotionGatePolicy(
        id="cli-policy",
        rules=[
            {
                "role": "validation",
                "min_total": 6,
                "min_candidate_score": 0.8,
                "min_absolute_improvement": 0.8,
                "min_discordant_pairs": 5,
                "max_one_sided_p_value": 0.05,
            }
        ],
    )
    proposal = PromotionProposal(
        id="cli-proposal",
        baseline_variant_id="baseline",
        baseline_variant_sha256=BASE_SHA,
        candidate_variant_id="candidate",
        candidate_variant_sha256=CANDIDATE_SHA,
        evidence=[evidence],
    )
    policy_path = tmp_path / "policy.yaml"
    proposal_path = tmp_path / "proposal.yaml"
    decision_path = tmp_path / "decision.json"
    _write(policy_path, policy)
    _write(proposal_path, proposal)
    result = runner.invoke(
        promotion_app,
        [
            "evaluate",
            str(policy_path),
            str(proposal_path),
            "--require-promotion",
            "--output",
            str(decision_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    decision = PromotionDecision.model_validate(json.loads(decision_path.read_text()))

    rollback = RollbackPlan(
        id="cli-rollback",
        promotion_decision_sha256=promotion_decision_sha256(decision),
        active_variant_id="candidate",
        active_variant_sha256=CANDIDATE_SHA,
        target_variant_id="baseline",
        target_variant_sha256=BASE_SHA,
        triggers=[
            {
                "id": "error-rate",
                "metric": "error_rate",
                "operator": "gt",
                "threshold": 0.1,
                "min_samples": 20,
            }
        ],
    )
    rollback_path = tmp_path / "rollback.yaml"
    receipt_path = tmp_path / "receipt.json"
    _write(rollback_path, rollback)
    result = runner.invoke(
        promotion_app,
        [
            "materialize",
            str(decision_path),
            str(rollback_path),
            "--output",
            str(receipt_path),
        ],
    )
    assert result.exit_code == 0, result.stdout

    event_path = tmp_path / "event.json"
    result = runner.invoke(
        promotion_app,
        [
            "rollback",
            str(receipt_path),
            "--trigger",
            "error-rate",
            "--value",
            "0.2",
            "--samples",
            "20",
            "--event-id",
            "cli-event",
            "--actor",
            "operator",
            "--occurred-at",
            "2026-08-07T00:00:00+00:00",
            "--output",
            str(event_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert json.loads(event_path.read_text())["trigger_id"] == "error-rate"


def test_cli_schema_and_digest_commands(tmp_path: Path) -> None:
    paths = _files(tmp_path)
    result = runner.invoke(promotion_app, ["schema", "--kind", "receipt"])
    assert result.exit_code == 0
    assert "PromotionReceipt" in result.stdout

    result = runner.invoke(
        promotion_app,
        ["digest", str(paths["baseline"]), "--kind", "predictions"],
    )
    assert result.exit_code == 0
    assert len(result.stdout.strip()) == 64


def test_cli_rejection_and_error_paths(tmp_path: Path) -> None:
    paths = _files(tmp_path)
    bad_payload = yaml.safe_load(paths["baseline"].read_text())
    bad_payload["predictions"].pop()
    paths["baseline"].write_text(yaml.safe_dump(bad_payload), encoding="utf-8")
    result = runner.invoke(
        promotion_app,
        [
            "compare",
            str(paths["manifest"]),
            str(paths["dataset"]),
            str(paths["baseline"]),
            str(paths["candidate"]),
            "--evidence-id",
            "bad",
        ],
    )
    assert result.exit_code == 2
    assert "missing examples" in result.stderr

    evidence = PairedEvaluationReport(
        evidence_id="reject-evidence",
        role="validation",
        dataset_id="cli",
        partition_id="cli-split",
        public_dataset_sha256="3" * 64,
        public_partition_sha256="4" * 64,
        baseline_variant_id="baseline",
        baseline_variant_sha256=BASE_SHA,
        candidate_variant_id="candidate",
        candidate_variant_sha256=CANDIDATE_SHA,
        total=1,
        both_correct=1,
        baseline_only_correct=0,
        candidate_only_correct=0,
        neither_correct=0,
        baseline_score=1,
        candidate_score=1,
    )
    policy = PromotionGatePolicy(id="reject", rules=[{"role": "validation", "min_total": 2}])
    proposal = PromotionProposal(
        id="reject",
        baseline_variant_id="baseline",
        baseline_variant_sha256=BASE_SHA,
        candidate_variant_id="candidate",
        candidate_variant_sha256=CANDIDATE_SHA,
        evidence=[evidence],
    )
    policy_path = tmp_path / "reject-policy.yaml"
    proposal_path = tmp_path / "reject-proposal.yaml"
    _write(policy_path, policy)
    _write(proposal_path, proposal)
    result = runner.invoke(
        promotion_app,
        [
            "evaluate",
            str(policy_path),
            str(proposal_path),
            "--require-promotion",
        ],
    )
    assert result.exit_code == 1
    assert '"decision": "reject"' in result.stdout
