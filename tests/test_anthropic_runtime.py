from __future__ import annotations

import json
from pathlib import Path

import pytest

from e2h.anthropic_runtime import (
    AnthropicHTTPResult,
    AnthropicMessagesInvocation,
    AnthropicRuntimeError,
    build_anthropic_messages_request,
    load_anthropic_messages_invocation,
    run_anthropic_messages,
)
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.variants import (
    HarnessVariant,
    HarnessVariantDocument,
    PromptMessage,
    ReferencedContextItem,
    WorkflowVariant,
)


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runtime-base",
            "goal": "Run one provider turn.",
            "success": {"commands": [{"id": "contract", "argv": ["python", "-c", "print('ok')"]}]},
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
            "id": "anthropic-candidate",
            "prompt": {
                "id": "runtime-prompt",
                "variables": ["task"],
                "messages": [
                    {
                        "id": "system",
                        "role": "system",
                        "content": "Preserve observable evidence.",
                    },
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
                        "content": "Use only supplied context.",
                        "max_chars": 26,
                        "placement": "before_prompt",
                    }
                ],
            },
            "routing": {
                "id": "runtime-routing",
                "targets": [
                    {
                        "id": "fast",
                        "provider": "anthropic",
                        "model": "claude-test-fast",
                        "capabilities": ["text", "tools"],
                    },
                    {
                        "id": "fallback",
                        "provider": "anthropic",
                        "model": "claude-test-fallback",
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


def invocation() -> AnthropicMessagesInvocation:
    return AnthropicMessagesInvocation(
        id="runtime-001",
        variables={"task": "the deterministic check"},
        route_metadata={"tier": "fast"},
        max_output_tokens=256,
    )


def response_payload(*, tool_name: str | None = "lookup") -> dict[str, object]:
    content: list[dict[str, object]] = []
    if tool_name is None:
        content.append({"type": "text", "text": "Done."})
        stop_reason = "end_turn"
    else:
        content.append(
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": tool_name,
                "input": {"key": "value"},
            }
        )
        stop_reason = "tool_use"
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test-fast",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def test_build_request_verifies_variant_and_maps_anthropic_semantics() -> None:
    request = build_anthropic_messages_request(document(), capsule(), invocation())

    assert request.route_target_id == "fast"
    assert request.model == "claude-test-fast"
    assert request.anthropic_version == "2023-06-01"
    assert request.body["max_tokens"] == 256
    assert request.body["system"] == [
        {"type": "text", "text": "Use only supplied context."},
        {"type": "text", "text": "Preserve observable evidence."},
        {"type": "text", "text": "Follow the evaluation contract."},
    ]
    assert request.body["messages"] == [
        {"role": "user", "content": "Execute the deterministic check."}
    ]
    assert request.body["tools"] == [
        {
            "name": "lookup",
            "description": "Look up one deterministic value.",
            "input_schema": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
                "additionalProperties": False,
            },
        }
    ]
    assert request.body["tool_choice"] == {
        "type": "tool",
        "name": "lookup",
        "disable_parallel_tool_use": True,
    }


def test_build_request_maps_required_auto_none_and_parallel_tool_policy() -> None:
    required = build_anthropic_messages_request(
        document(tool_selection="required"), capsule(), invocation()
    )
    assert required.body["tool_choice"] == {
        "type": "any",
        "disable_parallel_tool_use": True,
    }

    automatic_doc = document(tool_selection="auto")
    assert automatic_doc.variant.tools is not None
    automatic_doc.variant.tools.parallel_calls = True
    automatic = build_anthropic_messages_request(automatic_doc, capsule(), invocation())
    assert automatic.body["tool_choice"] == {
        "type": "auto",
        "disable_parallel_tool_use": False,
    }

    none = build_anthropic_messages_request(
        document(tool_selection="none"), capsule(), invocation()
    )
    assert none.body["tool_choice"] == {"type": "none"}


def test_build_request_uses_fallback_route_and_exact_variables() -> None:
    fallback = invocation().model_copy(update={"route_metadata": {}})
    request = build_anthropic_messages_request(document(), capsule(), fallback)
    assert request.route_target_id == "fallback"
    assert request.model == "claude-test-fallback"

    with pytest.raises(AnthropicRuntimeError, match="missing prompt variables"):
        build_anthropic_messages_request(
            document(),
            capsule(),
            invocation().model_copy(update={"variables": {}}),
        )
    with pytest.raises(AnthropicRuntimeError, match="undeclared prompt variables"):
        build_anthropic_messages_request(
            document(),
            capsule(),
            invocation().model_copy(update={"variables": {"task": "x", "surprise": "y"}}),
        )


def test_run_archives_request_response_and_headers_without_key_leakage() -> None:
    captured: dict[str, object] = {}

    def fake_transport(
        endpoint: str,
        body: bytes,
        headers: object,
        timeout_seconds: float,
    ) -> AnthropicHTTPResult:
        captured["endpoint"] = endpoint
        captured["body"] = json.loads(body)
        captured["headers"] = headers
        captured["timeout"] = timeout_seconds
        return AnthropicHTTPResult(payload=response_payload(), request_id="req_123")

    result = run_anthropic_messages(
        document(),
        capsule(),
        invocation(),
        api_key="test-secret",
        transport=fake_transport,
    )

    assert result.accepted
    assert result.provider_request_id == "req_123"
    assert captured["endpoint"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"] == {
        "x-api-key": "test-secret",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": "e2h-anthropic-runtime/0.28",
    }
    record = result.archive.records[0]
    assert record.request_id == "req_123"
    assert record.messages[0].id == "runtime-001.input.0"
    assert record.messages[0].content == "Execute the deterministic check."
    assert record.system == [
        {"type": "text", "text": "Use only supplied context."},
        {"type": "text", "text": "Preserve observable evidence."},
        {"type": "text", "text": "Follow the evaluation contract."},
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
    ) -> AnthropicHTTPResult:
        del endpoint, body, headers, timeout_seconds
        return AnthropicHTTPResult(payload=response_payload(tool_name=None))

    result = run_anthropic_messages(
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


def test_runtime_rejects_unfaithful_provider_neutral_semantics() -> None:
    base = document()

    non_anthropic = base.model_copy(deep=True)
    assert non_anthropic.variant.routing is not None
    non_anthropic.variant.routing.targets[0].provider = "openai"
    with pytest.raises(AnthropicRuntimeError, match="not 'anthropic'"):
        build_anthropic_messages_request(non_anthropic, capsule(), invocation())

    workflow = base.model_copy(deep=True)
    workflow.variant.workflow = WorkflowVariant.model_validate(
        {
            "id": "workflow",
            "stages": [{"id": "solve", "kind": "model", "handler": "solve"}],
        }
    )
    workflow = HarnessVariantDocument.model_validate(workflow.model_dump(mode="json"))
    with pytest.raises(AnthropicRuntimeError, match="does not execute workflow DAGs"):
        build_anthropic_messages_request(workflow, capsule(), invocation())

    referenced = base.model_copy(deep=True)
    assert referenced.variant.context is not None
    referenced.variant.context.items = [
        ReferencedContextItem.model_validate(
            {
                "id": "artifact",
                "kind": "artifact",
                "sha256": "1" * 64,
                "locator": "cas://artifact/one",
                "max_chars": 16,
            }
        )
    ]
    referenced = HarnessVariantDocument.model_validate(referenced.model_dump(mode="json"))
    with pytest.raises(AnthropicRuntimeError, match="does not dereference"):
        build_anthropic_messages_request(referenced, capsule(), invocation())

    after = base.model_copy(deep=True)
    assert after.variant.context is not None
    literal = after.variant.context.items[0]
    literal.placement = "after_prompt"
    with pytest.raises(AnthropicRuntimeError, match="after_prompt"):
        build_anthropic_messages_request(after, capsule(), invocation())

    late_system = base.model_copy(deep=True)
    assert late_system.variant.prompt is not None
    late_system.variant.prompt.messages.append(
        PromptMessage(id="late-system", role="system", content="Too late.")
    )
    with pytest.raises(AnthropicRuntimeError, match="top-level only"):
        build_anthropic_messages_request(
            HarnessVariantDocument.model_validate(late_system.model_dump(mode="json")),
            capsule(),
            invocation(),
        )


def test_runtime_rejects_invalid_key_and_parallel_provider_calls() -> None:
    with pytest.raises(AnthropicRuntimeError, match="not header-safe"):
        run_anthropic_messages(document(), capsule(), invocation(), api_key="bad\nkey")

    parallel_doc = document(tool_selection="auto")

    def fake_transport(
        endpoint: str,
        body: bytes,
        headers: object,
        timeout_seconds: float,
    ) -> AnthropicHTTPResult:
        del endpoint, body, headers, timeout_seconds
        payload = response_payload()
        payload["content"] = [
            {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"key": "a"}},
            {"type": "tool_use", "id": "toolu_2", "name": "lookup", "input": {"key": "b"}},
        ]
        return AnthropicHTTPResult(payload=payload)

    result = run_anthropic_messages(
        parallel_doc,
        capsule(),
        invocation(),
        api_key="test-secret",
        transport=fake_transport,
    )
    assert not result.accepted
    assert (
        "provider returned parallel tool calls despite parallel_calls=false"
        in result.policy_violations
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
max_output_tokens: 512
""",
        encoding="utf-8",
    )
    loaded = load_anthropic_messages_invocation(path)
    assert loaded.id == "runtime-002"
    assert loaded.variables == {"task": "verify it"}
    assert loaded.max_output_tokens == 512

    path.write_text("id: bad\nunknown: true\n", encoding="utf-8")
    with pytest.raises(AnthropicRuntimeError, match="invalid Anthropic Messages invocation"):
        load_anthropic_messages_invocation(path)
