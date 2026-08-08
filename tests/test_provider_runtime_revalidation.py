from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel

import e2h.anthropic_runtime as anthropic
import e2h.gemini_runtime as gemini
import e2h.openai_runtime as openai
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.variants import HarnessVariantDocument

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


@dataclass(frozen=True)
class ProviderCase:
    name: str
    provider: str
    model: str
    invocation_type: type[BaseModel]
    error_type: type[ValueError]


CASES = (
    ProviderCase(
        name="openai",
        provider="openai",
        model="openai-test",
        invocation_type=openai.OpenAIResponsesInvocation,
        error_type=openai.OpenAIRuntimeError,
    ),
    ProviderCase(
        name="anthropic",
        provider="anthropic",
        model="claude-test",
        invocation_type=anthropic.AnthropicMessagesInvocation,
        error_type=anthropic.AnthropicRuntimeError,
    ),
    ProviderCase(
        name="gemini",
        provider="google",
        model="gemini-test",
        invocation_type=gemini.GeminiGenerateContentInvocation,
        error_type=gemini.GeminiRuntimeError,
    ),
)


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runtime-revalidation",
            "goal": "Verify one provider runtime boundary.",
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


def document(case: ProviderCase) -> HarnessVariantDocument:
    base = capsule()
    return HarnessVariantDocument.model_validate(
        {
            "base_capsule_sha256": capsule_sha256(base),
            "variant": {
                "id": f"{case.name}-runtime",
                "prompt": {
                    "id": "prompt",
                    "variables": ["task"],
                    "messages": [
                        {
                            "id": "system",
                            "role": "system",
                            "content": "Preserve observable evidence.",
                        },
                        {
                            "id": "user",
                            "role": "user",
                            "content": "Execute ${task}.",
                        },
                    ],
                },
                "tools": {
                    "id": "tools",
                    "tools": [
                        {
                            "id": "lookup",
                            "description": "Look up one value.",
                            "input_schema": {"type": "object"},
                        }
                    ],
                    "selection": "auto",
                    "parallel_calls": False,
                    "max_calls": 1,
                },
                "routing": {
                    "id": "routing",
                    "targets": [
                        {
                            "id": "primary",
                            "provider": case.provider,
                            "model": case.model,
                            "capabilities": ["text", "tools"],
                        }
                    ],
                    "rules": [],
                    "fallback_target": "primary",
                },
            },
        }
    )


def invocation(case: ProviderCase) -> BaseModel:
    return case.invocation_type.model_validate(
        {
            "id": "conformance-001",
            "variables": {"task": "the deterministic check"},
            "max_output_tokens": 128,
            "timeout_seconds": 3.0,
            "metadata": {"phase": "initial"},
        }
    )


def mutate_model(model: BaseModel, **updates: object) -> None:
    for name, value in updates.items():
        setattr(model, name, value)


def build_request(
    case: ProviderCase,
    current_document: HarnessVariantDocument,
    current_capsule: TaskCapsule,
    current_invocation: BaseModel,
) -> BaseModel:
    if case.name == "openai":
        assert isinstance(current_invocation, openai.OpenAIResponsesInvocation)
        return openai.build_openai_responses_request(
            current_document,
            current_capsule,
            current_invocation,
        )
    if case.name == "anthropic":
        assert isinstance(current_invocation, anthropic.AnthropicMessagesInvocation)
        return anthropic.build_anthropic_messages_request(
            current_document,
            current_capsule,
            current_invocation,
        )
    assert isinstance(current_invocation, gemini.GeminiGenerateContentInvocation)
    return gemini.build_gemini_generate_content_request(
        current_document,
        current_capsule,
        current_invocation,
    )


def response_payload(case: ProviderCase) -> dict[str, Any]:
    if case.name == "openai":
        return {
            "id": "resp_1",
            "object": "response",
            "created_at": 1_786_089_600,
            "model": case.model,
            "output": [
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "lookup",
                    "arguments": "{}",
                    "status": "completed",
                }
            ],
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
    if case.name == "anthropic":
        return {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": case.model,
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "lookup",
                    "input": {},
                }
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    return {
        "responseId": "response_1",
        "modelVersion": f"{case.model}-001",
        "candidates": [
            {
                "index": 0,
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "id": "call_1",
                                "name": "lookup",
                                "args": {},
                            }
                        }
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
    }


def run_runtime(
    case: ProviderCase,
    current_document: HarnessVariantDocument,
    current_capsule: TaskCapsule,
    current_invocation: BaseModel,
    observed: dict[str, float],
    *,
    during_transport: Callable[[], None] | None = None,
) -> BaseModel:
    def record_transport(timeout_seconds: float) -> None:
        observed["timeout"] = timeout_seconds
        if during_transport is not None:
            during_transport()

    if case.name == "openai":
        assert isinstance(current_invocation, openai.OpenAIResponsesInvocation)

        def transport(
            endpoint: str,
            body: bytes,
            headers: Mapping[str, str],
            timeout_seconds: float,
        ) -> openai.OpenAIHTTPResult:
            del endpoint, body, headers
            record_transport(timeout_seconds)
            return openai.OpenAIHTTPResult(payload=response_payload(case), request_id="request_1")

        return openai.run_openai_responses(
            current_document,
            current_capsule,
            current_invocation,
            api_key="test-key",
            transport=transport,
        )

    if case.name == "anthropic":
        assert isinstance(current_invocation, anthropic.AnthropicMessagesInvocation)

        def transport(
            endpoint: str,
            body: bytes,
            headers: Mapping[str, str],
            timeout_seconds: float,
        ) -> anthropic.AnthropicHTTPResult:
            del endpoint, body, headers
            record_transport(timeout_seconds)
            return anthropic.AnthropicHTTPResult(
                payload=response_payload(case),
                request_id="request_1",
            )

        return anthropic.run_anthropic_messages(
            current_document,
            current_capsule,
            current_invocation,
            api_key="test-key",
            transport=transport,
        )

    assert isinstance(current_invocation, gemini.GeminiGenerateContentInvocation)

    def transport(
        endpoint: str,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> gemini.GeminiHTTPResult:
        del endpoint, body, headers
        record_transport(timeout_seconds)
        return gemini.GeminiHTTPResult(payload=response_payload(case), request_id="request_1")

    return gemini.run_gemini_generate_content(
        current_document,
        current_capsule,
        current_invocation,
        api_key="test-key",
        transport=transport,
    )


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_builders_revalidate_mutated_invocation_bounds(case: ProviderCase) -> None:
    current_invocation = invocation(case)
    mutate_model(current_invocation, max_output_tokens=0)

    with pytest.raises(case.error_type, match=r"invalid .* invocation"):
        build_request(case, document(case), capsule(), current_invocation)


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_builders_revalidate_mutated_variant_cross_fields(case: ProviderCase) -> None:
    current_document = document(case)
    assert current_document.variant.prompt is not None
    current_document.variant.prompt.variables = []

    with pytest.raises(case.error_type, match="invalid variant document"):
        build_request(case, current_document, capsule(), invocation(case))


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_builders_revalidate_mutated_capsules(case: ProviderCase) -> None:
    current_capsule = capsule()
    current_capsule.goal = ""

    with pytest.raises(case.error_type, match="invalid task capsule"):
        build_request(case, document(case), current_capsule, invocation(case))


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_builders_revalidate_without_serializer_warnings(case: ProviderCase) -> None:
    current_document = document(case)
    mutate_model(
        current_document.variant,
        workflow={
            "id": "workflow",
            "stages": [{"id": "solve", "kind": "model", "handler": "solve"}],
        },
    )

    with pytest.raises(case.error_type, match="does not execute workflow DAGs"):
        build_request(case, current_document, capsule(), invocation(case))


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_runs_accept_valid_post_construction_mutation(case: ProviderCase) -> None:
    current_invocation = invocation(case)
    mutate_model(current_invocation, timeout_seconds=7.5)
    mutate_model(current_invocation, metadata={"phase": "mutated"})
    observed: dict[str, float] = {}

    result = run_runtime(
        case,
        document(case),
        capsule(),
        current_invocation,
        observed,
    )

    payload = result.model_dump(mode="python")
    assert observed == {"timeout": 7.5}
    assert payload["archive"]["metadata"]["phase"] == "mutated"


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_runs_keep_using_normalized_snapshots_after_transport_starts(case: ProviderCase) -> None:
    current_document = document(case)
    current_capsule = capsule()
    current_invocation = invocation(case)
    mutate_model(current_invocation, metadata={"phase": "normalized"})
    observed: dict[str, float] = {}

    def mutate_originals() -> None:
        mutate_model(current_invocation, id="late-mutation")
        mutate_model(current_invocation, metadata={"phase": "late"})
        current_capsule.id = "late-capsule"
        assert current_document.variant.tools is not None
        current_document.variant.tools.max_calls = 0

    result = run_runtime(
        case,
        current_document,
        current_capsule,
        current_invocation,
        observed,
        during_transport=mutate_originals,
    )

    payload = result.model_dump(mode="python")
    assert observed == {"timeout": 3.0}
    assert payload["policy_violations"] == []
    assert payload["archive"]["id"] == "conformance-001"
    assert payload["archive"]["capsule_id"] == "runtime-revalidation"
    assert payload["archive"]["metadata"]["phase"] == "normalized"
