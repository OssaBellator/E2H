from __future__ import annotations

import re

import pytest
from pydantic import BaseModel

from e2h.anthropic_runtime import (
    AnthropicMessagesInvocation,
    AnthropicRuntimeError,
)
from e2h.gemini_runtime import (
    GeminiGenerateContentInvocation,
    GeminiRuntimeError,
)
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.openai_runtime import (
    OpenAIResponsesInvocation,
    OpenAIRuntimeError,
)
from e2h.runtime_validation import revalidate_runtime_inputs
from e2h.variants import HarnessVariantDocument


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runtime-validation",
            "goal": "Validate one runtime input boundary.",
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
            "variant": {"id": "runtime-validation-variant"},
        }
    )


@pytest.mark.parametrize(
    ("invocation", "expected_type", "error_type", "noun"),
    [
        (
            AnthropicMessagesInvocation(id="anthropic-runtime"),
            OpenAIResponsesInvocation,
            OpenAIRuntimeError,
            "OpenAI Responses invocation",
        ),
        (
            GeminiGenerateContentInvocation(id="gemini-runtime"),
            AnthropicMessagesInvocation,
            AnthropicRuntimeError,
            "Anthropic Messages invocation",
        ),
        (
            OpenAIResponsesInvocation(id="openai-runtime"),
            GeminiGenerateContentInvocation,
            GeminiRuntimeError,
            "Gemini GenerateContent invocation",
        ),
    ],
    ids=["openai", "anthropic", "gemini"],
)
def test_revalidation_rejects_structurally_compatible_wrong_provider_invocation(
    invocation: BaseModel,
    expected_type: type[BaseModel],
    error_type: type[ValueError],
    noun: str,
) -> None:
    expected_message = (
        rf"invalid {re.escape(noun)}: expected {expected_type.__name__}, "
        rf"got {type(invocation).__name__}"
    )
    with pytest.raises(error_type, match=expected_message):
        revalidate_runtime_inputs(
            document(),
            capsule(),
            invocation,
            expected_type,
            error_type=error_type,
            invocation_noun=noun,
        )
