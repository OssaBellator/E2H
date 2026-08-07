from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.openai_runtime import (
    OpenAIHTTPResult,
    OpenAIResponsesInvocation,
    OpenAIRuntimeError,
    build_openai_responses_request,
    load_openai_responses_invocation,
    run_openai_responses,
)
from e2h.variants import HarnessVariant, HarnessVariantDocument


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runtime-base",
            "goal": "Run one provider turn.",
            "success": {
                "commands": [
                    {"id": "contract", "argv": ["python", "-c", "print('ok')"]}
                ]
            },
        }
    )


def document(*, tool_selection: str = "named") -> HarnessVariantDocument:
    base = capsule()
    tool_payload: dict[str, object] = {
        "id": "runtime-tools",
        "tools": [
            {
                "id": "lookup",
                "description": "Look up one deterministic value.",
                "input_schema": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                    "additionalProperties": False,
                },
            }
        ],
        "selection": tool_selection,
        "parallel_calls": False,
        "max_calls": 1,
    }
    if tool_selection == "named":
        tool_payload["selected_tool"] = "lookup"
    variant = HarnessVariant.model_validate(
        {
            "id": "openai-candidate",
            "prompt": {
                "id": "runtime-prompt",
                "variables": ["task"],
                "messages": [
                    {
                        "id": "developer",
                        "role": "developer",
                        "content": "Follow the evaluation contract.",
                    },
                    {
                        "id": "user",
                        "role": "user",
                        "content": "Execute ${task}.",
                    },
                ],
            },
            "tools": tool_payload,
            "context": {
                "id": "runtime-context",
                "max_chars": 64,
                "overflow": "reject",
                "items": [
                    {
                        "id": "literal",
                        "kind": "literal",
                        "content": "Use observable evidence only.",
                        "max_chars": 29,
                        "placement": "before_prompt",
                    }
                ],
            },
            "routing": {
                "id": "runtime-routing",
                "targets": [
                    {
                        "id": "fast",
                        "provider": "openai",
                        "model": "gpt-test-fast",
                        "capabilities": ["text", "tools"],
                    },
                    {
                        "id": "fallback",
                        "provider": "openai",
                        "model": "gpt-test-fallback",
                        "capabilities": ["text", "tools"],
                    },
                ],
                "rules": [
                    {
                        "id": "fast-route",
                        "match": {"tier": "fast"},
                        "target_id": "fast",
                        "priority": 10,
                    }
                ],
                "fallback_target": "fallback",
            },
        }
    )
    return HarnessVariantDocument(
        base_capsule_sha256=capsule_sha256(base),
        variant=variant,
    )


def invocation() -> OpenAIResponsesInvocation:
    return OpenAIResponsesInvocation(
        id="runtime-001",
        variables={"task": "the deterministic check"},
        route_metadata={"tier": "fast"},
        max_output_tokens=256,
    )


def response_payload(*, tool_name: str | None = "lookup") -> dict[str, object]:
    output: list[dict[str, object]] = []
    if tool_name is not None:
        output.append(
            {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": tool_name,
                "arguments": json.dumps({"key": "value"}),
                "status": "completed",
            }
        )
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1_786_089_600,
        "model": "gpt-test-fast",
        "output": output,
        "status": "completed",
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }


def test_build_request_verifies_variant_and_renders_typed_dimensions() -> None:
    request = build_openai_responses_request(document(), capsule(), invocation())

    assert request.route_target_id == "fast"
    assert request.model == "gpt-test-fast"
    assert request.body["store"] is False
    assert request.body["max_output_tokens"] == 256
    assert request.body["input"] == [
        {"role": "developer", "content": "Use observable evidence only."},
        {"role": "developer", "content": "Follow the evaluation contract."},
        {"role": "user", "content": "Execute the deterministic check."},
    ]
    assert request.body["tools"] == [
        {
            "type": "function",
            "name": "lookup",
            "description": "Look up one deterministic value.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
                "additionalProperties": False,
            },
        }
    ]
    assert request.body["tool_choice"] == {"type": "function", "name": "lookup"}
    assert request.body["parallel_tool_calls"] is False
    assert request.body["metadata"]["e2h_variant_id"] == "openai-candidate"


def test_build_request_uses_fallback_route_and_exact_variables() -> None:
    fallback = invocation().model_copy(update={"route_metadata": {}})
    request = build_openai_responses_request(document(), capsule(), fallback)
    assert request.route_target_id == "fallback"
    assert request.model == "gpt-test-fallback"

    with pytest.raises(OpenAIRuntimeError, match="missing prompt variables"):
        build_openai_responses_request(
            document(),
            capsule(),
            invocation().model_copy(update={"variables": {}}),
        )
    with pytest.raises(OpenAIRuntimeError, match="undeclared prompt variables"):
        build_openai_responses_request(
            document(),
            capsule(),
            invocation().model_copy(
                update={"variables": {"task": "x", "surprise": "y"}}
            ),
        )


def test_run_archives_observable_request_and_response_without_key_leakage() -> None:
    captured: dict[str, object] = {}

    def fake_transport(
        endpoint: str,
        body: bytes,
        headers: object,
        timeout_seconds: float,
    ) -> OpenAIHTTPResult:
        captured["endpoint"] = endpoint
        captured["body"] = json.loads(body)
        captured["headers"] = headers
        captured["timeout"] = timeout_seconds
        return OpenAIHTTPResult(payload=response_payload(), request_id="req_123")

    result = run_openai_responses(
        document(),
        capsule(),
        invocation(),
        api_key="test-secret",
        transport=fake_transport,
    )

    assert result.accepted
    assert result.provider_request_id == "req_123"
    assert captured["endpoint"] == "https://api.openai.com/v1/responses"
    assert result.archive.responses[0].request_id == "req_123"
    assert result.archive.responses[0].input_items[2]["id"] == "runtime-001.input.2"
    assert result.archive.responses[0].input_items[2]["content"] == [
        {"type": "input_text", "text": "Execute the deterministic check."}
    ]
    serialized = result.model_dump_json()
    assert "test-secret" not in serialized
    assert result.archive.metadata["request_sha256"] == result.request.request_sha256


def test_run_records_tool_policy_violations_instead_of_dropping_evidence() -> None:
    required = document(tool_selection="required")

    def fake_transport(
        endpoint: str,
        body: bytes,
        headers: object,
        timeout_seconds: float,
    ) -> OpenAIHTTPResult:
        del endpoint, body, headers, timeout_seconds
        return OpenAIHTTPResult(payload=response_payload(tool_name=None))

    result = run_openai_responses(
        required,
        capsule(),
        invocation(),
        api_key="test-secret",
        transport=fake_transport,
    )

    assert not result.accepted
    assert result.policy_violations == [
        "provider returned no tool call despite selection='required'"
    ]
    assert result.archive.metadata["tool_policy_violations"] == result.policy_violations


def test_runtime_rejects_non_openai_routes_workflows_and_referenced_context() -> None:
    base = document()
    local = base.model_copy(deep=True)
    assert local.variant.routing is not None
    local.variant.routing.targets[0].provider = "local"
    with pytest.raises(OpenAIRuntimeError, match="not 'openai'"):
        build_openai_responses_request(local, capsule(), invocation())

    workflow = base.model_copy(deep=True)
    workflow.variant.workflow = {
        "id": "workflow",
        "stages": [{"id": "solve", "kind": "model", "handler": "solve"}],
    }
    with pytest.raises(OpenAIRuntimeError, match="does not execute workflow DAGs"):
        build_openai_responses_request(
            HarnessVariantDocument.model_validate(workflow.model_dump(mode="json")),
            capsule(),
            invocation(),
        )

    referenced = base.model_copy(deep=True)
    assert referenced.variant.context is not None
    referenced.variant.context.items = [
        {
            "id": "artifact",
            "kind": "artifact",
            "sha256": "1" * 64,
            "locator": "cas://artifact/one",
            "max_chars": 16,
        }
    ]
    with pytest.raises(OpenAIRuntimeError, match="does not dereference"):
        build_openai_responses_request(
            HarnessVariantDocument.model_validate(referenced.model_dump(mode="json")),
            capsule(),
            invocation(),
        )


def test_invocation_loader_is_strict(tmp_path: Path) -> None:
    path = tmp_path / "invocation.yaml"
    path.write_text(
        """schema_version: "0.1"
id: runtime-002
variables:
  task: verify it
route_metadata:
  tier: fast
""",
        encoding="utf-8",
    )
    loaded = load_openai_responses_invocation(path)
    assert loaded.id == "runtime-002"
    assert loaded.variables == {"task": "verify it"}

    path.write_text("id: bad\nunknown: true\n", encoding="utf-8")
    with pytest.raises(OpenAIRuntimeError, match="invalid OpenAI Responses invocation"):
        load_openai_responses_invocation(path)
