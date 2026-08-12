from __future__ import annotations

import re

import pytest
from pydantic import BaseModel, ConfigDict

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


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


class _DocumentSubclass(HarnessVariantDocument):
    pass


class _CapsuleSubclass(TaskCapsule):
    pass


class _OpenAIInvocationSubclass(OpenAIResponsesInvocation):
    pass


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


def _lookalike(value: BaseModel) -> _Lookalike:
    return _Lookalike.model_validate(value.model_dump(mode="json"))


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


def test_revalidation_rejects_structurally_compatible_wrong_document_type() -> None:
    wrong_document = _lookalike(document())

    with pytest.raises(
        OpenAIRuntimeError,
        match=r"invalid variant document: expected HarnessVariantDocument, got _Lookalike",
    ):
        revalidate_runtime_inputs(
            wrong_document,
            capsule(),
            OpenAIResponsesInvocation(id="openai-runtime"),
            OpenAIResponsesInvocation,
            error_type=OpenAIRuntimeError,
            invocation_noun="OpenAI Responses invocation",
        )


def test_revalidation_rejects_structurally_compatible_wrong_capsule_type() -> None:
    wrong_capsule = _lookalike(capsule())

    with pytest.raises(
        OpenAIRuntimeError,
        match=r"invalid task capsule: expected TaskCapsule, got _Lookalike",
    ):
        revalidate_runtime_inputs(
            document(),
            wrong_capsule,
            OpenAIResponsesInvocation(id="openai-runtime"),
            OpenAIResponsesInvocation,
            error_type=OpenAIRuntimeError,
            invocation_noun="OpenAI Responses invocation",
        )


def test_revalidation_rejects_document_subclasses() -> None:
    subclassed = _DocumentSubclass.model_validate(document().model_dump(mode="json"))

    with pytest.raises(
        OpenAIRuntimeError,
        match=(r"invalid variant document: expected HarnessVariantDocument, got _DocumentSubclass"),
    ):
        revalidate_runtime_inputs(
            subclassed,
            capsule(),
            OpenAIResponsesInvocation(id="openai-runtime"),
            OpenAIResponsesInvocation,
            error_type=OpenAIRuntimeError,
            invocation_noun="OpenAI Responses invocation",
        )


def test_revalidation_rejects_capsule_subclasses() -> None:
    subclassed = _CapsuleSubclass.model_validate(capsule().model_dump(mode="json"))

    with pytest.raises(
        OpenAIRuntimeError,
        match=r"invalid task capsule: expected TaskCapsule, got _CapsuleSubclass",
    ):
        revalidate_runtime_inputs(
            document(),
            subclassed,
            OpenAIResponsesInvocation(id="openai-runtime"),
            OpenAIResponsesInvocation,
            error_type=OpenAIRuntimeError,
            invocation_noun="OpenAI Responses invocation",
        )


def test_revalidation_rejects_invocation_subclasses() -> None:
    subclassed = _OpenAIInvocationSubclass(id="openai-runtime")

    with pytest.raises(
        OpenAIRuntimeError,
        match=(
            r"invalid OpenAI Responses invocation: expected OpenAIResponsesInvocation, "
            r"got _OpenAIInvocationSubclass"
        ),
    ):
        revalidate_runtime_inputs(
            document(),
            capsule(),
            subclassed,
            OpenAIResponsesInvocation,
            error_type=OpenAIRuntimeError,
            invocation_noun="OpenAI Responses invocation",
        )


def test_revalidation_rejects_canonical_invalid_python_values() -> None:
    current_capsule = capsule()
    current_capsule.metadata = {"not_json": {"value"}}

    with pytest.raises(OpenAIRuntimeError, match=r"invalid task capsule:.*canonical JSON"):
        revalidate_runtime_inputs(
            document(),
            current_capsule,
            OpenAIResponsesInvocation(id="openai-runtime"),
            OpenAIResponsesInvocation,
            error_type=OpenAIRuntimeError,
            invocation_noun="OpenAI Responses invocation",
        )
