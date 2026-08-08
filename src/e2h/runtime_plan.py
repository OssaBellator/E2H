"""Credential-free request planning across E2H provider runtime adapters."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from e2h.anthropic_runtime import (
    AnthropicMessagesInvocation,
    AnthropicMessagesRequest,
    AnthropicRuntimeError,
    build_anthropic_messages_request,
    load_anthropic_messages_invocation,
)
from e2h.gemini_runtime import (
    GeminiGenerateContentInvocation,
    GeminiGenerateContentRequest,
    GeminiRuntimeError,
    build_gemini_generate_content_request,
    load_gemini_generate_content_invocation,
)
from e2h.loader import CapsuleLoadError, load_capsule
from e2h.models import TaskCapsule
from e2h.openai_runtime import (
    OpenAIResponsesInvocation,
    OpenAIResponsesRequest,
    OpenAIRuntimeError,
    build_openai_responses_request,
    load_openai_responses_invocation,
)
from e2h.runtime_validation import revalidate_runtime_model
from e2h.variants import (
    HarnessVariantDocument,
    VariantError,
    load_variant_document,
)


class RuntimePlanError(ValueError):
    """Raised when a provider request plan cannot be materialized safely."""


class RuntimeProvider(StrEnum):
    """Live provider runtimes supported by the unified planner."""

    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    GEMINI_GENERATE_CONTENT = "gemini-generate-content"


RuntimeInvocation = (
    OpenAIResponsesInvocation | AnthropicMessagesInvocation | GeminiGenerateContentInvocation
)
RuntimeRequest = OpenAIResponsesRequest | AnthropicMessagesRequest | GeminiGenerateContentRequest


class RuntimeRequestPlan(BaseModel):
    """One exact provider request materialized without credentials or network I/O."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    provider: RuntimeProvider
    request: RuntimeRequest

    @model_validator(mode="after")
    def request_type_must_match_provider(self) -> RuntimeRequestPlan:
        expected: dict[RuntimeProvider, type[BaseModel]] = {
            RuntimeProvider.OPENAI_RESPONSES: OpenAIResponsesRequest,
            RuntimeProvider.ANTHROPIC_MESSAGES: AnthropicMessagesRequest,
            RuntimeProvider.GEMINI_GENERATE_CONTENT: GeminiGenerateContentRequest,
        }
        if not isinstance(self.request, expected[self.provider]):
            raise ValueError("runtime request type does not match provider")
        return self

    @property
    def request_sha256(self) -> str:
        """Return the underlying provider request digest."""
        return self.request.request_sha256


def _provider(value: RuntimeProvider | str) -> RuntimeProvider:
    try:
        return RuntimeProvider(value)
    except ValueError as exc:
        supported = ", ".join(item.value for item in RuntimeProvider)
        raise RuntimePlanError(
            f"unsupported runtime provider {value!r}; expected one of: {supported}"
        ) from exc


def _invocation_for_provider(
    provider: RuntimeProvider,
    invocation: RuntimeInvocation,
) -> RuntimeInvocation:
    expected: dict[RuntimeProvider, type[BaseModel]] = {
        RuntimeProvider.OPENAI_RESPONSES: OpenAIResponsesInvocation,
        RuntimeProvider.ANTHROPIC_MESSAGES: AnthropicMessagesInvocation,
        RuntimeProvider.GEMINI_GENERATE_CONTENT: GeminiGenerateContentInvocation,
    }
    if not isinstance(invocation, expected[provider]):
        raise RuntimePlanError(
            f"invocation type {type(invocation).__name__} does not match provider "
            f"{provider.value!r}"
        )
    return invocation


def plan_runtime_request(
    provider: RuntimeProvider | str,
    document: HarnessVariantDocument,
    capsule: TaskCapsule,
    invocation: RuntimeInvocation,
) -> RuntimeRequestPlan:
    """Materialize one exact provider request without credentials or network I/O."""
    selected = _provider(provider)
    checked = _invocation_for_provider(selected, invocation)
    try:
        if selected is RuntimeProvider.OPENAI_RESPONSES:
            assert isinstance(checked, OpenAIResponsesInvocation)
            openai_invocation = revalidate_runtime_model(
                checked,
                OpenAIResponsesInvocation,
                error_type=RuntimePlanError,
                noun="openai-responses invocation",
            )
            request: RuntimeRequest = build_openai_responses_request(
                document,
                capsule,
                openai_invocation,
            )
        elif selected is RuntimeProvider.ANTHROPIC_MESSAGES:
            assert isinstance(checked, AnthropicMessagesInvocation)
            anthropic_invocation = revalidate_runtime_model(
                checked,
                AnthropicMessagesInvocation,
                error_type=RuntimePlanError,
                noun="anthropic-messages invocation",
            )
            request = build_anthropic_messages_request(
                document,
                capsule,
                anthropic_invocation,
            )
        else:
            assert isinstance(checked, GeminiGenerateContentInvocation)
            gemini_invocation = revalidate_runtime_model(
                checked,
                GeminiGenerateContentInvocation,
                error_type=RuntimePlanError,
                noun="gemini-generate-content invocation",
            )
            request = build_gemini_generate_content_request(
                document,
                capsule,
                gemini_invocation,
            )
    except (OpenAIRuntimeError, AnthropicRuntimeError, GeminiRuntimeError) as exc:
        raise RuntimePlanError(f"unable to plan {selected.value} request: {exc}") from exc
    return RuntimeRequestPlan(provider=selected, request=request)


def _load_invocation(
    provider: RuntimeProvider,
    path: Path,
) -> RuntimeInvocation:
    try:
        if provider is RuntimeProvider.OPENAI_RESPONSES:
            return load_openai_responses_invocation(path)
        if provider is RuntimeProvider.ANTHROPIC_MESSAGES:
            return load_anthropic_messages_invocation(path)
        return load_gemini_generate_content_invocation(path)
    except (OpenAIRuntimeError, AnthropicRuntimeError, GeminiRuntimeError) as exc:
        raise RuntimePlanError(f"unable to load {provider.value} invocation: {exc}") from exc


def load_runtime_request_plan(
    provider: RuntimeProvider | str,
    capsule_path: Path,
    variant_path: Path,
    invocation_path: Path,
) -> RuntimeRequestPlan:
    """Load E2H documents and materialize one credential-free provider request plan."""
    selected = _provider(provider)
    try:
        capsule = load_capsule(capsule_path)
        document = load_variant_document(variant_path)
    except (CapsuleLoadError, VariantError) as exc:
        raise RuntimePlanError(f"unable to load runtime plan inputs: {exc}") from exc
    invocation = _load_invocation(selected, invocation_path)
    return plan_runtime_request(selected, document, capsule, invocation)


__all__ = [
    "RuntimeInvocation",
    "RuntimePlanError",
    "RuntimeProvider",
    "RuntimeRequest",
    "RuntimeRequestPlan",
    "load_runtime_request_plan",
    "plan_runtime_request",
]
