from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from e2h.failures import unexpected_exit_failure
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.optimizer_adapters import (
    DSPyDatasetDocument,
    DSPyExample,
    OptimizerAdapterDocument,
    OptimizerAdapterError,
    OptimizerCandidateDocument,
    OptimizerKind,
    PromptComponentBinding,
    apply_optimizer_candidate,
    dspy_dataset_payload,
    feedback_from_run_result,
    gepa_prediction_payload,
    load_optimizer_adapter,
    optimizer_adapter_sha256,
    optimizer_candidate_sha256,
    verify_optimizer_adapter,
)
from e2h.runner import CheckStatus, CommandResult, RunResult, RunStatus
from e2h.variants import HarnessVariant, HarnessVariantDocument, variant_sha256


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "optimizer-base",
            "goal": "Evaluate an optimizer candidate.",
            "success": {
                "commands": [
                    {
                        "id": "contract",
                        "argv": ["python", "-c", "print('ok')"],
                    }
                ]
            },
        }
    )


def variant_document() -> HarnessVariantDocument:
    base = capsule()
    return HarnessVariantDocument(
        base_capsule_sha256=capsule_sha256(base),
        variant=HarnessVariant.model_validate(
            {
                "id": "baseline",
                "prompt": {
                    "id": "prompt",
                    "variables": ["task"],
                    "messages": [
                        {
                            "id": "system",
                            "role": "system",
                            "content": "Follow the task contract.",
                        },
                        {
                            "id": "user",
                            "role": "user",
                            "content": "Execute ${task}.",
                        },
                    ],
                },
                "metadata": {"generation": 0},
            }
        ),
    )


def adapter_document(
    optimizer: OptimizerKind = OptimizerKind.GEPA,
) -> OptimizerAdapterDocument:
    document = variant_document()
    return OptimizerAdapterDocument(
        id="prompt-optimizer",
        optimizer=optimizer,
        base_capsule_sha256=document.base_capsule_sha256,
        base_variant_sha256=variant_sha256(document.variant),
        components=[
            PromptComponentBinding(
                id="system-instruction",
                message_id="system",
                description="Optimize the system instruction.",
            )
        ],
    )


def candidate_document(
    adapter: OptimizerAdapterDocument,
    *,
    content: str = "Follow the task contract and cite observable evidence.",
) -> OptimizerCandidateDocument:
    return OptimizerCandidateDocument(
        candidate_id="gepa-0001",
        variant_id="candidate-0001",
        optimizer=adapter.optimizer,
        adapter_sha256=optimizer_adapter_sha256(adapter),
        base_capsule_sha256=adapter.base_capsule_sha256,
        base_variant_sha256=adapter.base_variant_sha256,
        updates=[{"component_id": "system-instruction", "content": content}],
        score=0.75,
    )


def test_dspy_dataset_exports_plain_example_payloads() -> None:
    dataset = DSPyDatasetDocument(
        id="dataset",
        examples=[
            DSPyExample(
                id="one",
                inputs={"question": "2+2"},
                outputs={"answer": "4"},
            ),
            DSPyExample(
                id="two",
                inputs={"question": "3+3"},
                outputs={"answer": "6"},
            ),
        ],
    )

    payloads = dspy_dataset_payload(dataset)

    assert payloads[0].values == {"question": "2+2", "answer": "4"}
    assert payloads[0].input_fields == ["question"]
    assert payloads[1].values["answer"] == "6"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "id": "bad",
                "inputs": {"question": "x"},
                "outputs": {"question": "y"},
            },
            "overlap",
        ),
        (
            {
                "id": "bad",
                "inputs": {"bad-key": "x"},
            },
            "identifiers",
        ),
        (
            {
                "id": "bad",
                "inputs": {"question": float("nan")},
            },
            "canonical JSON",
        ),
    ],
)
def test_dspy_example_rejects_ambiguous_or_noncanonical_fields(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        DSPyExample.model_validate(payload)


def test_dspy_dataset_requires_one_consistent_signature() -> None:
    with pytest.raises(ValidationError, match="identical input fields"):
        DSPyDatasetDocument(
            id="mixed",
            examples=[
                DSPyExample(id="one", inputs={"question": "x"}),
                DSPyExample(id="two", inputs={"prompt": "y"}),
            ],
        )
    with pytest.raises(ValidationError, match="ids must be unique"):
        DSPyDatasetDocument(
            id="duplicates",
            examples=[
                DSPyExample(id="same", inputs={"question": "x"}),
                DSPyExample(id="same", inputs={"question": "y"}),
            ],
        )


def test_adapter_verification_and_candidate_application_are_digest_bound() -> None:
    base = capsule()
    variant = variant_document()
    adapter = adapter_document()
    verification = verify_optimizer_adapter(adapter, base, variant)

    assert verification.optimizer is OptimizerKind.GEPA
    assert verification.adapter_sha256 == optimizer_adapter_sha256(adapter)
    assert verification.component_ids == ["system-instruction"]

    candidate = candidate_document(adapter)
    result = apply_optimizer_candidate(adapter, candidate, base, variant)

    assert result.variant.id == "candidate-0001"
    assert result.variant.prompt is not None
    assert result.variant.prompt.messages[0].content.endswith("observable evidence.")
    assert result.variant.prompt.messages[1].content == "Execute ${task}."
    provenance = result.variant.metadata["e2h_optimizer"]
    assert provenance["candidate_id"] == "gepa-0001"
    assert provenance["candidate_sha256"] == optimizer_candidate_sha256(candidate)
    assert provenance["score"] == 0.75
    assert result.base_capsule_sha256 == capsule_sha256(base)


def test_adapter_rejects_unknown_messages_and_identity_mismatches() -> None:
    base = capsule()
    variant = variant_document()
    adapter = adapter_document()
    adapter.components[0].message_id = "missing"
    with pytest.raises(OptimizerAdapterError, match="unknown prompt message"):
        verify_optimizer_adapter(adapter, base, variant)

    adapter = adapter_document()
    candidate = candidate_document(adapter)
    candidate.base_variant_sha256 = "0" * 64
    with pytest.raises(OptimizerAdapterError, match="base variant digest"):
        apply_optimizer_candidate(adapter, candidate, base, variant)


def test_candidate_rejects_unknown_noop_and_reserved_provenance() -> None:
    base = capsule()
    variant = variant_document()
    adapter = adapter_document()

    candidate = candidate_document(adapter)
    candidate.updates[0].component_id = "missing"
    with pytest.raises(OptimizerAdapterError, match="undeclared"):
        apply_optimizer_candidate(adapter, candidate, base, variant)

    candidate = candidate_document(adapter, content="Follow the task contract.")
    with pytest.raises(OptimizerAdapterError, match="change at least one"):
        apply_optimizer_candidate(adapter, candidate, base, variant)

    variant.variant.metadata["e2h_optimizer"] = {"existing": True}
    candidate = candidate_document(adapter)
    with pytest.raises(OptimizerAdapterError, match="reserved key"):
        apply_optimizer_candidate(adapter, candidate, base, variant)


def test_candidate_revalidation_preserves_prompt_variable_contract() -> None:
    base = capsule()
    variant = variant_document()
    adapter = OptimizerAdapterDocument(
        id="user-optimizer",
        optimizer="dspy",
        base_capsule_sha256=variant.base_capsule_sha256,
        base_variant_sha256=variant_sha256(variant.variant),
        components=[{"id": "user-prompt", "message_id": "user"}],
    )
    candidate = OptimizerCandidateDocument(
        candidate_id="bad-variable",
        variant_id="bad-variable",
        optimizer="dspy",
        adapter_sha256=optimizer_adapter_sha256(adapter),
        base_capsule_sha256=adapter.base_capsule_sha256,
        base_variant_sha256=adapter.base_variant_sha256,
        updates=[{"component_id": "user-prompt", "content": "Do the work."}],
    )

    with pytest.raises(ValidationError, match="unused variables"):
        apply_optimizer_candidate(adapter, candidate, base, variant)


def test_feedback_is_gepa_compatible_and_excludes_raw_output() -> None:
    now = datetime.now(UTC)
    failure = unexpected_exit_failure(7, [0])
    result = RunResult(
        capsule_id="feedback",
        status=RunStatus.FAILED,
        started_at=now,
        finished_at=now,
        duration_seconds=0,
        checks=[
            CommandResult(
                id="pass",
                argv=["python"],
                cwd=".",
                status=CheckStatus.PASSED,
                exit_code=0,
                duration_seconds=0,
                stdout="secret-pass-output",
            ),
            CommandResult(
                id="fail",
                argv=["python"],
                cwd=".",
                status=CheckStatus.FAILED,
                exit_code=7,
                duration_seconds=0,
                stderr="secret-fail-output",
                failure=failure,
            ),
        ],
        failure_summary={
            "total": 1,
            "evaluation_failures": 1,
            "by_category": {"task": 1},
            "by_code": {"unexpected_exit": 1},
            "primary_check_id": "fail",
            "primary_code": "unexpected_exit",
        },
    )

    feedback = feedback_from_run_result(result)
    prediction = gepa_prediction_payload(feedback)

    assert feedback.score == 0.5
    assert prediction["score"] == 0.5
    assert "unexpected_exit" in feedback.feedback
    assert "secret-pass-output" not in feedback.feedback
    assert "secret-fail-output" not in feedback.feedback
    assert feedback.checks[1].failure_impact == "evaluation_failure"


def test_adapter_loader_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "adapter.yaml"
    path.write_text(
        "schema_version: '0.1'\nid: one\nid: two\n",
        encoding="utf-8",
    )
    with pytest.raises(OptimizerAdapterError, match="duplicate key"):
        load_optimizer_adapter(path)


def test_adapter_document_round_trip_yaml(tmp_path: Path) -> None:
    adapter = adapter_document()
    path = tmp_path / "adapter.yaml"
    path.write_text(
        yaml.safe_dump(adapter.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )

    loaded = load_optimizer_adapter(path)

    assert loaded == adapter
    assert json.loads(loaded.model_dump_json())["optimizer"] == "gepa"
