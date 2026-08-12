from __future__ import annotations

from typing import Any, TypeVar, cast

import pytest
from pydantic import BaseModel

from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.optimizer_adapters import (
    OptimizerAdapterDocument,
    OptimizerAdapterError,
    OptimizerCandidateDocument,
    OptimizerKind,
    apply_optimizer_candidate,
    optimizer_adapter_sha256,
    verify_optimizer_adapter,
)
from e2h.variants import HarnessVariant, HarnessVariantDocument, variant_sha256

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

ModelT = TypeVar("ModelT", bound=BaseModel)


class _AdapterSubclass(OptimizerAdapterDocument):
    pass


class _CandidateSubclass(OptimizerCandidateDocument):
    pass


class _CapsuleSubclass(TaskCapsule):
    pass


class _VariantDocumentSubclass(HarnessVariantDocument):
    pass


def _as_subclass(value: BaseModel, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(value.model_dump(mode="json"))


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "optimizer-boundary",
            "goal": "Evaluate one optimizer candidate.",
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


def variant_document(base: TaskCapsule | None = None) -> HarnessVariantDocument:
    base = base or capsule()
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
            }
        ),
    )


def adapter_document(
    base: TaskCapsule | None = None,
    variant: HarnessVariantDocument | None = None,
) -> OptimizerAdapterDocument:
    base = base or capsule()
    variant = variant or variant_document(base)
    return OptimizerAdapterDocument(
        id="prompt-optimizer",
        optimizer=OptimizerKind.GEPA,
        base_capsule_sha256=capsule_sha256(base),
        base_variant_sha256=variant_sha256(variant.variant),
        components=[
            {
                "id": "system-instruction",
                "message_id": "system",
            }
        ],
    )


def candidate_document(adapter: OptimizerAdapterDocument) -> OptimizerCandidateDocument:
    return OptimizerCandidateDocument(
        candidate_id="candidate-1",
        variant_id="candidate-1",
        optimizer=adapter.optimizer,
        adapter_sha256=optimizer_adapter_sha256(adapter),
        base_capsule_sha256=adapter.base_capsule_sha256,
        base_variant_sha256=adapter.base_variant_sha256,
        updates=[
            {
                "component_id": "system-instruction",
                "content": "Follow the task contract and preserve evidence.",
            }
        ],
    )


def test_verification_revalidates_mutated_adapter_cross_fields() -> None:
    base = capsule()
    variant = variant_document(base)
    adapter = adapter_document(base, variant)
    adapter.components.append(adapter.components[0])

    with pytest.raises(OptimizerAdapterError, match="invalid optimizer adapter"):
        verify_optimizer_adapter(adapter, base, variant)


def test_verification_rejects_canonical_invalid_adapter_metadata() -> None:
    base = capsule()
    variant = variant_document(base)
    adapter = adapter_document(base, variant)
    adapter.metadata = {"not_json": {"value"}}

    with pytest.raises(OptimizerAdapterError, match="invalid optimizer adapter"):
        verify_optimizer_adapter(adapter, base, variant)


def test_verification_revalidates_capsule_before_digest_binding() -> None:
    base = capsule()
    variant = variant_document(base)
    adapter = adapter_document(base, variant)
    base.goal = ""

    with pytest.raises(OptimizerAdapterError, match="invalid task capsule"):
        verify_optimizer_adapter(adapter, base, variant)


def test_verification_revalidates_variant_cross_fields_before_hashing() -> None:
    base = capsule()
    variant = variant_document(base)
    adapter = adapter_document(base, variant)
    assert variant.variant.prompt is not None
    variant.variant.prompt.variables = []

    with pytest.raises(OptimizerAdapterError, match="invalid variant document"):
        verify_optimizer_adapter(adapter, base, variant)


def test_application_revalidates_mutated_candidate_cross_fields() -> None:
    base = capsule()
    variant = variant_document(base)
    adapter = adapter_document(base, variant)
    candidate = candidate_document(adapter)
    candidate.updates.append(candidate.updates[0])

    with pytest.raises(OptimizerAdapterError, match="invalid optimizer candidate"):
        apply_optimizer_candidate(adapter, candidate, base, variant)


@pytest.mark.parametrize("kind", ["adapter", "candidate", "capsule", "variant"])
def test_optimizer_boundaries_reject_model_subclasses(kind: str) -> None:
    base = capsule()
    variant = variant_document(base)
    adapter = adapter_document(base, variant)
    candidate = candidate_document(adapter)

    if kind == "adapter":
        adapter = cast(Any, _as_subclass(adapter, _AdapterSubclass))
        expected = "expected OptimizerAdapterDocument, got _AdapterSubclass"
    elif kind == "candidate":
        candidate = cast(Any, _as_subclass(candidate, _CandidateSubclass))
        expected = "expected OptimizerCandidateDocument, got _CandidateSubclass"
    elif kind == "capsule":
        base = cast(Any, _as_subclass(base, _CapsuleSubclass))
        expected = "expected TaskCapsule, got _CapsuleSubclass"
    else:
        variant = cast(Any, _as_subclass(variant, _VariantDocumentSubclass))
        expected = "expected HarnessVariantDocument, got _VariantDocumentSubclass"

    with pytest.raises(OptimizerAdapterError, match=expected):
        apply_optimizer_candidate(adapter, candidate, base, variant)


def test_verification_normalizes_warning_prone_component_assignment() -> None:
    base = capsule()
    variant = variant_document(base)
    adapter = adapter_document(base, variant)
    adapter.components = [
        {
            "id": "system-instruction",
            "message_id": "system",
        }
    ]

    verification = verify_optimizer_adapter(adapter, base, variant)

    assert verification.component_ids == ["system-instruction"]
