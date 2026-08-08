from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel

from e2h.anthropic_runtime import (
    AnthropicMessagesInvocation,
    AnthropicMessagesRequest,
)
from e2h.gemini_runtime import (
    GeminiGenerateContentInvocation,
    GeminiGenerateContentRequest,
)
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.openai_runtime import (
    OpenAIResponsesInvocation,
    OpenAIResponsesRequest,
)
from e2h.runtime_plan import (
    RuntimePlanError,
    RuntimeProvider,
    RuntimeRequestPlan,
    load_runtime_request_plan,
    plan_runtime_request,
)
from e2h.variants import HarnessVariantDocument


@dataclass(frozen=True)
class ProviderCase:
    provider: RuntimeProvider
    route_provider: str
    model: str
    invocation_type: type[BaseModel]
    request_type: type[BaseModel]


CASES = (
    ProviderCase(
        provider=RuntimeProvider.OPENAI_RESPONSES,
        route_provider="openai",
        model="openai-test",
        invocation_type=OpenAIResponsesInvocation,
        request_type=OpenAIResponsesRequest,
    ),
    ProviderCase(
        provider=RuntimeProvider.ANTHROPIC_MESSAGES,
        route_provider="anthropic",
        model="claude-test",
        invocation_type=AnthropicMessagesInvocation,
        request_type=AnthropicMessagesRequest,
    ),
    ProviderCase(
        provider=RuntimeProvider.GEMINI_GENERATE_CONTENT,
        route_provider="google",
        model="gemini-test",
        invocation_type=GeminiGenerateContentInvocation,
        request_type=GeminiGenerateContentRequest,
    ),
)


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runtime-plan",
            "goal": "Plan one live provider request.",
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


def document(case: ProviderCase, *, route_provider: str | None = None) -> HarnessVariantDocument:
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
                "context": {
                    "id": "context",
                    "items": [
                        {
                            "id": "literal",
                            "kind": "literal",
                            "content": "Use supplied context.",
                            "max_chars": 21,
                            "placement": "before_prompt",
                        }
                    ],
                },
                "routing": {
                    "id": "routing",
                    "targets": [
                        {
                            "id": "primary",
                            "provider": route_provider or case.route_provider,
                            "model": case.model,
                            "capabilities": ["text"],
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
            "id": "plan-001",
            "variables": {"task": "the deterministic check"},
            "max_output_tokens": 128,
        }
    )


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.provider.value)
def test_plan_runtime_request_preserves_native_request_and_digest(
    case: ProviderCase,
) -> None:
    current_invocation = invocation(case)
    first = plan_runtime_request(
        case.provider,
        document(case),
        capsule(),
        current_invocation,
    )
    second = plan_runtime_request(
        case.provider.value,
        document(case),
        capsule(),
        current_invocation,
    )

    assert isinstance(first, RuntimeRequestPlan)
    assert first == second
    assert first.provider is case.provider
    assert isinstance(first.request, case.request_type)
    assert first.request_sha256 == first.request.request_sha256
    assert len(first.request_sha256) == 64
    assert first.request.invocation_id == "plan-001"
    assert first.request.route_target_id == "primary"
    assert first.request.model == case.model


@pytest.mark.parametrize("case", CASES, ids=lambda item: item.provider.value)
def test_load_runtime_request_plan_is_file_backed_and_credential_free(
    case: ProviderCase,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule_path = tmp_path / "capsule.json"
    variant_path = tmp_path / "variant.json"
    invocation_path = tmp_path / "invocation.json"
    capsule_path.write_text(capsule().model_dump_json(indent=2) + "\n", encoding="utf-8")
    variant_path.write_text(
        document(case).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    invocation_path.write_text(
        invocation(case).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    plan = load_runtime_request_plan(
        case.provider,
        capsule_path,
        variant_path,
        invocation_path,
    )

    assert plan.provider is case.provider
    assert isinstance(plan.request, case.request_type)
    assert plan.request.invocation_id == "plan-001"


def test_plan_rejects_unknown_provider_and_mismatched_invocation() -> None:
    openai_case = CASES[0]
    anthropic_case = CASES[1]

    with pytest.raises(RuntimePlanError, match="unsupported runtime provider"):
        plan_runtime_request(
            "other-provider",
            document(openai_case),
            capsule(),
            invocation(openai_case),
        )

    with pytest.raises(RuntimePlanError, match="does not match provider"):
        plan_runtime_request(
            RuntimeProvider.OPENAI_RESPONSES,
            document(openai_case),
            capsule(),
            invocation(anthropic_case),
        )


def test_plan_normalizes_provider_build_failures() -> None:
    openai_case = CASES[0]
    with pytest.raises(RuntimePlanError, match="unable to plan openai-responses request"):
        plan_runtime_request(
            RuntimeProvider.OPENAI_RESPONSES,
            document(openai_case, route_provider="anthropic"),
            capsule(),
            invocation(openai_case),
        )


def test_runtime_request_plan_rejects_provider_request_type_mismatch() -> None:
    anthropic_case = CASES[1]
    anthropic_plan = plan_runtime_request(
        RuntimeProvider.ANTHROPIC_MESSAGES,
        document(anthropic_case),
        capsule(),
        invocation(anthropic_case),
    )

    with pytest.raises(ValueError, match="runtime request type does not match provider"):
        RuntimeRequestPlan(
            provider=RuntimeProvider.OPENAI_RESPONSES,
            request=anthropic_plan.request,
        )


def test_runtime_request_plan_rejects_unknown_schema_version() -> None:
    openai_case = CASES[0]
    plan = plan_runtime_request(
        RuntimeProvider.OPENAI_RESPONSES,
        document(openai_case),
        capsule(),
        invocation(openai_case),
    )
    payload = plan.model_dump()
    payload["schema_version"] = "999"

    with pytest.raises(ValueError):
        RuntimeRequestPlan.model_validate(payload)


def test_load_runtime_request_plan_normalizes_invocation_errors(tmp_path: Path) -> None:
    openai_case = CASES[0]
    capsule_path = tmp_path / "capsule.json"
    variant_path = tmp_path / "variant.json"
    invocation_path = tmp_path / "invocation.json"
    capsule_path.write_text(capsule().model_dump_json(indent=2) + "\n", encoding="utf-8")
    variant_path.write_text(
        document(openai_case).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    invocation_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimePlanError, match="unable to load openai-responses invocation"):
        load_runtime_request_plan(
            RuntimeProvider.OPENAI_RESPONSES,
            capsule_path,
            variant_path,
            invocation_path,
        )


def test_load_runtime_request_plan_normalizes_document_errors(tmp_path: Path) -> None:
    bad_capsule = tmp_path / "capsule.json"
    variant = tmp_path / "variant.json"
    invocation_path = tmp_path / "invocation.json"
    bad_capsule.write_text("{}\n", encoding="utf-8")
    variant.write_text("{}\n", encoding="utf-8")
    invocation_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimePlanError, match="unable to load runtime plan inputs"):
        load_runtime_request_plan(
            RuntimeProvider.OPENAI_RESPONSES,
            bad_capsule,
            variant,
            invocation_path,
        )
