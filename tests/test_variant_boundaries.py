from __future__ import annotations

from typing import Any, TypeVar, cast

import pytest
from pydantic import BaseModel

from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.variants import (
    HarnessVariant,
    HarnessVariantDocument,
    VariantError,
    verify_variant_document,
)

pytestmark = pytest.mark.filterwarnings("error::UserWarning")

ModelT = TypeVar("ModelT", bound=BaseModel)


class _DocumentSubclass(HarnessVariantDocument):
    pass


class _CapsuleSubclass(TaskCapsule):
    pass


def _as_subclass(value: BaseModel, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(value.model_dump(mode="json"))


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "variant-boundary",
            "goal": "Verify one typed variant.",
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


def document(base: TaskCapsule | None = None) -> HarnessVariantDocument:
    base = base or capsule()
    return HarnessVariantDocument(
        base_capsule_sha256=capsule_sha256(base),
        variant=HarnessVariant.model_validate(
            {
                "id": "candidate",
                "prompt": {
                    "id": "prompt",
                    "variables": ["task"],
                    "messages": [
                        {
                            "id": "user",
                            "role": "user",
                            "content": "Execute ${task}.",
                        }
                    ],
                },
            }
        ),
    )


def test_verification_revalidates_mutated_variant_cross_fields() -> None:
    base = capsule()
    variant = document(base)
    assert variant.variant.prompt is not None
    variant.variant.prompt.variables = []

    with pytest.raises(VariantError, match="invalid variant document"):
        verify_variant_document(variant, base)


def test_verification_revalidates_capsule_before_digest_binding() -> None:
    base = capsule()
    variant = document(base)
    base.goal = ""

    with pytest.raises(VariantError, match="invalid task capsule"):
        verify_variant_document(variant, base)


def test_verification_rejects_model_subclasses_and_plain_wrong_types() -> None:
    base = capsule()
    variant = document(base)

    with pytest.raises(
        VariantError, match="expected HarnessVariantDocument, got _DocumentSubclass"
    ):
        verify_variant_document(_as_subclass(variant, _DocumentSubclass), base)

    with pytest.raises(VariantError, match="expected TaskCapsule, got _CapsuleSubclass"):
        verify_variant_document(variant, _as_subclass(base, _CapsuleSubclass))

    with pytest.raises(VariantError, match="expected HarnessVariantDocument, got object"):
        verify_variant_document(cast(Any, object()), base)


def test_verification_normalizes_warning_prone_prompt_assignment() -> None:
    base = capsule()
    variant = document(base)
    assert variant.variant.prompt is not None
    variant.variant.prompt = variant.variant.prompt.model_dump(mode="json")

    verification = verify_variant_document(variant, base)

    assert verification.variant_id == "candidate"
