from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel

import e2h.anthropic_runtime as anthropic
import e2h.gemini_runtime as gemini
import e2h.openai_runtime as openai
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.variants import (
    ContextVariant,
    HarnessVariantDocument,
    RoutingVariant,
    ToolVariant,
)


@dataclass(frozen=True)
class ProviderCase:
    name: str
    provider: str
    model: str
    invocation_type: type[BaseModel]
    error_type: type[Exception]


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
            "id": "runtime-conformance",
            "goal": "Verify one provider-neutral runtime turn.",
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


def document(
    case: ProviderCase,
    *,
    provider: str | None = None,
    workflow: bool = False,
    referenced_context: bool = False,
) -> HarnessVariantDocument:
    if referenced_context:
        context_items: list[dict[str, object]] = [
            {
                "id": "artifact",
                "kind": "artifact",
                "sha256": "1" * 64,
                "locator": "cas://runtime/artifact",
                "max_chars": 16,
            }
        ]
    else:
        context_items = [
            {
                "id": "literal",
                "kind": "literal",
                "content": "context",
                "max_chars": 7,
                "placement": "before_prompt",
            }
        ]

    variant: dict[str, object] = {
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
        "context": {
            "id": "context",
            "max_chars": 64,
            "overflow": "reject",
            "items": context_items,
        },
        "routing": {
            "id": "routing",
            "targets": [
                {
                    "id": "primary",
                    "provider": provider or case.provider,
                    "model": case.model,
                    "capabilities": ["text", "tools"],
                }
            ],
            "rules": [],
            "fallback_target": "primary",
        },
    }
    if workflow:
        variant["workflow"] = {
            "id": "workflow",
            "stages": [
                {
                    "id": "solve",
                    "kind": "model",
                    "handler": "solve",
                }
            ],
        }

    base = capsule()
    return HarnessVariantDocument.model_validate(
        {
            "base_capsule_sha256": capsule_sha256(base),
            "variant": variant,
        }
    )


def invocation(
    case: ProviderCase,
    *,
    variables: dict[str, str] | None = None,
) -> BaseModel:
    return case.invocation_type.model_validate(
        {
            "id": "conformance-001",
            "variables": ({"task": "the deterministic check"} if variables is None else variables),
        }
    )


def build_request(
    case: ProviderCase,
    doc: HarnessVariantDocument,
    *,
    variables: dict[str, str] | None = None,
) -> BaseModel:
    current_invocation = invocation(case, variables=variables)
    if case.name == "openai":
        assert isinstance(
            current_invocation,
            openai.OpenAIResponsesInvocation,
        )
        return openai.build_openai_responses_request(
            doc,
            capsule(),
            current_invocation,
        )
    if case.name == "anthropic":
        assert isinstance(
            current_invocation,
            anthropic.AnthropicMessagesInvocation,
        )
        return anthropic.build_anthropic_messages_request(
            doc,
            capsule(),
            current_invocation,
        )
    assert isinstance(
        current_invocation,
        gemini.GeminiGenerateContentInvocation,
    )
    return gemini.build_gemini_generate_content_request(
        doc,
        capsule(),
        current_invocation,
    )


def tool_variant(selection: str) -> ToolVariant:
    payload: dict[str, Any] = {
        "id": "tools",
        "tools": [
            {
                "id": "lookup",
                "description": "Look up one value.",
                "input_schema": {"type": "object"},
            }
        ],
        "selection": selection,
        "parallel_calls": False,
        "max_calls": 1,
    }
    if selection == "named":
        payload["selected_tool"] = "lookup"
    return ToolVariant.model_validate(payload)


def normalized_tool_choice(
    case: ProviderCase,
    tools: ToolVariant,
) -> tuple[str, str | None]:
    if case.name == "openai":
        _, choice, _ = openai._build_tools(tools)
        if isinstance(choice, str):
            return choice, None
        return "named", choice["name"]

    if case.name == "anthropic":
        _, choice = anthropic._build_tools(tools)
        assert choice is not None
        if choice["type"] == "tool":
            return "named", choice["name"]
        if choice["type"] == "any":
            return "required", None
        return choice["type"], None

    _, config = gemini._build_tools(tools)
    assert config is not None
    choice = config["functionCallingConfig"]
    if choice["mode"] == "ANY" and "allowedFunctionNames" in choice:
        return "named", choice["allowedFunctionNames"][0]
    mapping = {
        "AUTO": "auto",
        "ANY": "required",
        "NONE": "none",
    }
    return mapping[choice["mode"]], None


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_request_builders_preserve_shared_identity_contract(
    case: ProviderCase,
) -> None:
    doc = document(case)
    first = build_request(case, doc)
    second = build_request(case, doc)

    assert first == second
    payload = first.model_dump(mode="python")
    assert payload["invocation_id"] == "conformance-001"
    assert payload["route_target_id"] == "primary"
    assert payload["model"] == case.model
    assert payload["base_capsule_sha256"] == capsule_sha256(capsule())
    assert len(payload["request_sha256"]) == 64


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
@pytest.mark.parametrize("selection", ["auto", "required", "none", "named"])
def test_tool_selection_preserves_provider_neutral_intent(
    case: ProviderCase,
    selection: str,
) -> None:
    normalized, selected_tool = normalized_tool_choice(
        case,
        tool_variant(selection),
    )
    assert normalized == selection
    expected_tool = "lookup" if selection == "named" else None
    assert selected_tool == expected_tool


def test_context_truncation_is_provider_neutral_before_mapping() -> None:
    context = ContextVariant.model_validate(
        {
            "id": "context",
            "max_chars": 5,
            "overflow": "truncate_low_priority",
            "ordering": "priority",
            "items": [
                {
                    "id": "low",
                    "kind": "literal",
                    "content": "xyz",
                    "max_chars": 3,
                    "priority": 1,
                },
                {
                    "id": "high",
                    "kind": "literal",
                    "content": "abcd",
                    "max_chars": 4,
                    "priority": 100,
                },
            ],
        }
    )
    expected = [("high", "abcd"), ("low", "x")]
    for materialize in (
        openai._context_items,
        anthropic._context_items,
        gemini._context_items,
    ):
        items = materialize(context)
        assert [(item.id, item.content) for item in items] == expected


@pytest.mark.parametrize(
    ("materialize", "error_type"),
    [
        (openai._context_items, openai.OpenAIRuntimeError),
        (anthropic._context_items, anthropic.AnthropicRuntimeError),
        (gemini._context_items, gemini.GeminiRuntimeError),
    ],
)
def test_tool_context_is_rejected_by_every_provider(
    materialize: Any,
    error_type: type[Exception],
) -> None:
    context = ContextVariant.model_validate(
        {
            "id": "tool-context",
            "items": [
                {
                    "id": "literal",
                    "kind": "literal",
                    "content": "tool",
                    "max_chars": 4,
                    "placement": "tool_context",
                }
            ],
        }
    )
    with pytest.raises(error_type, match="tool_context"):
        materialize(context)


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_request_builders_require_exact_prompt_variables(
    case: ProviderCase,
) -> None:
    with pytest.raises(case.error_type, match="missing prompt variables"):
        build_request(case, document(case), variables={})
    with pytest.raises(
        case.error_type,
        match="undeclared prompt variables",
    ):
        build_request(
            case,
            document(case),
            variables={"task": "ok", "extra": "not declared"},
        )


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_request_builders_reject_workflow_execution(
    case: ProviderCase,
) -> None:
    with pytest.raises(
        case.error_type,
        match="does not execute workflow DAGs",
    ):
        build_request(case, document(case, workflow=True))


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_request_builders_reject_referenced_context(
    case: ProviderCase,
) -> None:
    with pytest.raises(case.error_type, match="does not dereference"):
        build_request(
            case,
            document(case, referenced_context=True),
        )


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.name)
def test_request_builders_reject_wrong_route_provider(
    case: ProviderCase,
) -> None:
    wrong_provider = {
        "openai": "anthropic",
        "anthropic": "google",
        "gemini": "openai",
    }[case.name]
    with pytest.raises(case.error_type):
        build_request(
            case,
            document(case, provider=wrong_provider),
        )


def test_route_priority_and_fallback_are_shared_before_provider_checks() -> None:
    routing = RoutingVariant.model_validate(
        {
            "id": "routing",
            "targets": [
                {
                    "id": "primary",
                    "provider": "openai",
                    "model": "primary-model",
                },
                {
                    "id": "fast",
                    "provider": "openai",
                    "model": "fast-model",
                },
            ],
            "rules": [
                {
                    "id": "low",
                    "match": {"tier": "fast"},
                    "target_id": "primary",
                    "priority": 1,
                },
                {
                    "id": "high",
                    "match": {"tier": "fast"},
                    "target_id": "fast",
                    "priority": 10,
                },
            ],
            "fallback_target": "primary",
        }
    )
    assert openai._select_route(routing, {"tier": "fast"}).id == "fast"
    assert openai._select_route(routing, {}).id == "primary"
