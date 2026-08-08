from __future__ import annotations

import pytest

from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.openai_runtime import OpenAIResponsesInvocation
from e2h.runtime_plan import RuntimePlanError, RuntimeProvider, plan_runtime_request
from e2h.variants import HarnessVariantDocument


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runtime-plan-revalidation",
            "goal": "Plan one provider request.",
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


def document() -> HarnessVariantDocument:
    base = capsule()
    return HarnessVariantDocument.model_validate(
        {
            "base_capsule_sha256": capsule_sha256(base),
            "variant": {
                "id": "runtime-plan-variant",
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
                "routing": {
                    "id": "routing",
                    "targets": [
                        {
                            "id": "primary",
                            "provider": "openai",
                            "model": "openai-test",
                        }
                    ],
                    "fallback_target": "primary",
                },
            },
        }
    )


def invocation() -> OpenAIResponsesInvocation:
    return OpenAIResponsesInvocation.model_validate(
        {
            "id": "plan-001",
            "variables": {"task": "the deterministic check"},
            "max_output_tokens": 128,
        }
    )


def test_plan_revalidates_mutated_variant_document() -> None:
    mutated = document()
    assert mutated.variant.prompt is not None
    mutated.variant.prompt.variables = []

    with pytest.raises(RuntimePlanError, match="invalid variant document"):
        plan_runtime_request(
            RuntimeProvider.OPENAI_RESPONSES,
            mutated,
            capsule(),
            invocation(),
        )


def test_plan_revalidates_mutated_capsule() -> None:
    mutated = capsule()
    mutated.goal = ""

    with pytest.raises(RuntimePlanError, match="invalid task capsule"):
        plan_runtime_request(
            RuntimeProvider.OPENAI_RESPONSES,
            document(),
            mutated,
            invocation(),
        )


def test_plan_revalidates_mutated_invocation() -> None:
    mutated = invocation()
    mutated.max_output_tokens = 0

    with pytest.raises(RuntimePlanError, match="invalid openai-responses invocation"):
        plan_runtime_request(
            RuntimeProvider.OPENAI_RESPONSES,
            document(),
            capsule(),
            mutated,
        )
