from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

import e2h.gemini_runtime as runtime
from e2h._version import VERSION
from e2h.gemini_runtime import (
    GeminiGenerateContentInvocation,
    GeminiGenerateContentRequest,
    GeminiHTTPResult,
    GeminiRuntimeError,
    build_gemini_generate_content_request,
    load_gemini_generate_content_invocation,
    run_gemini_generate_content,
)
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.variants import (
    ContextVariant,
    HarnessVariant,
    HarnessVariantDocument,
    PromptMessage,
    ReferencedContextItem,
    ToolVariant,
    WorkflowVariant,
)


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "runtime-base",
            "goal": "Run one provider turn.",
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
    *,
    tool_selection: str = "named",
    provider: str = "google",
) -> HarnessVariantDocument:
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
            "id": "gemini-candidate",
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
                    {
                        "id": "assistant",
                        "role": "assistant",
                        "content": "I will check it.",
                    },
                    {
                        "id": "user-two",
                        "role": "user",
                        "content": "Return observable evidence.",
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
                        "provider": provider,
                        "model": "gemini-test-fast",
                        "capabilities": ["text", "tools"],
                    },
                    {
                        "id": "fallback",
                        "provider": provider,
                        "model": "gemini-test-fallback",
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


def invocation() -> GeminiGenerateContentInvocation:
    return GeminiGenerateContentInvocation(
        id="runtime-001",
        variables={"task": "the deterministic check"},
        route_metadata={"tier": "fast"},
        max_output_tokens=256,
    )


def response_payload(*, tool_name: str | None = "lookup") -> dict[str, object]:
    if tool_name is None:
        parts: list[dict[str, object]] = [{"text": "Done."}]
    else:
        parts = [
            {
                "functionCall": {
                    "id": "call_1",
                    "name": tool_name,
                    "args": {"key": "value"},
                }
            }
        ]
    return {
        "responseId": "response_1",
        "modelVersion": "gemini-test-fast-001",
        "candidates": [
            {
                "index": 0,
                "content": {"role": "model", "parts": parts},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
        },
    }


def test_build_request_maps_prompt_tools_route_and_generation_config() -> None:
    request = build_gemini_generate_content_request(
        document(),
        capsule(),
        invocation(),
    )

    assert request.route_target_id == "fast"
    assert request.model == "gemini-test-fast"
    assert request.endpoint.endswith("/models/gemini-test-fast:generateContent")
    assert request.body["generationConfig"] == {"maxOutputTokens": 256}
    assert request.body["store"] is False
    assert request.body["systemInstruction"] == {
        "parts": [
            {"text": "Use only supplied context."},
            {"text": "Preserve observable evidence."},
            {"text": "Follow the evaluation contract."},
        ]
    }
    assert request.body["contents"] == [
        {
            "role": "user",
            "parts": [{"text": "Execute the deterministic check."}],
        },
        {"role": "model", "parts": [{"text": "I will check it."}]},
        {
            "role": "user",
            "parts": [{"text": "Return observable evidence."}],
        },
    ]
    assert request.body["tools"] == [
        {
            "functionDeclarations": [
                {
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
        }
    ]
    assert request.body["toolConfig"] == {
        "functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["lookup"],
        }
    }


def test_build_request_maps_tool_modes_and_provider_alias() -> None:
    required = build_gemini_generate_content_request(
        document(tool_selection="required"),
        capsule(),
        invocation(),
    )
    assert required.body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}

    automatic = build_gemini_generate_content_request(
        document(tool_selection="auto", provider="gemini"),
        capsule(),
        invocation(),
    )
    assert automatic.body["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}

    none = build_gemini_generate_content_request(
        document(tool_selection="none"),
        capsule(),
        invocation(),
    )
    assert none.body["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}


def test_build_request_uses_fallback_and_accepts_models_prefix() -> None:
    doc = document()
    assert doc.variant.routing is not None
    doc.variant.routing.targets[1].model = "models/gemini-test-fallback"
    fallback = invocation().model_copy(update={"route_metadata": {}})
    request = build_gemini_generate_content_request(doc, capsule(), fallback)
    assert request.route_target_id == "fallback"
    assert request.endpoint.endswith("/models/gemini-test-fallback:generateContent")


def test_build_request_rejects_variable_and_provider_contract_violations() -> None:
    with pytest.raises(GeminiRuntimeError, match="missing prompt variables"):
        build_gemini_generate_content_request(
            document(),
            capsule(),
            invocation().model_copy(update={"variables": {}}),
        )
    with pytest.raises(GeminiRuntimeError, match="undeclared prompt variables"):
        build_gemini_generate_content_request(
            document(),
            capsule(),
            invocation().model_copy(update={"variables": {"task": "x", "extra": "y"}}),
        )
    with pytest.raises(GeminiRuntimeError, match="not 'google' or 'gemini'"):
        build_gemini_generate_content_request(
            document(provider="openai"),
            capsule(),
            invocation(),
        )


def test_runtime_rejects_workflow_references_and_unfaithful_placement() -> None:
    base = document()

    workflow = base.model_copy(deep=True)
    workflow.variant.workflow = WorkflowVariant.model_validate(
        {
            "id": "workflow",
            "stages": [{"id": "solve", "kind": "model", "handler": "solve"}],
        }
    )
    workflow = HarnessVariantDocument.model_validate(workflow.model_dump(mode="json"))
    with pytest.raises(GeminiRuntimeError, match="does not execute workflow DAGs"):
        build_gemini_generate_content_request(workflow, capsule(), invocation())

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
    with pytest.raises(GeminiRuntimeError, match="does not dereference"):
        build_gemini_generate_content_request(referenced, capsule(), invocation())

    after = base.model_copy(deep=True)
    assert after.variant.context is not None
    after.variant.context.items[0].placement = "after_prompt"
    with pytest.raises(GeminiRuntimeError, match="after_prompt"):
        build_gemini_generate_content_request(after, capsule(), invocation())

    late_system = base.model_copy(deep=True)
    assert late_system.variant.prompt is not None
    late_system.variant.prompt.messages.append(
        PromptMessage(
            id="late-system",
            role="system",
            content="Too late.",
        )
    )
    with pytest.raises(GeminiRuntimeError, match="top-level only"):
        build_gemini_generate_content_request(
            late_system,
            capsule(),
            invocation(),
        )


def test_context_truncation_priority_and_tool_context_rejection() -> None:
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
    items = runtime._context_items(context)
    assert [(item.id, item.content) for item in items] == [
        ("high", "abcd"),
        ("low", "x"),
    ]

    tool_context = ContextVariant.model_validate(
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
    with pytest.raises(GeminiRuntimeError, match="tool_context"):
        runtime._context_items(tool_context)


def test_run_archives_response_context_and_never_serializes_key() -> None:
    captured: dict[str, object] = {}

    def fake_transport(
        endpoint: str,
        body: bytes,
        headers: object,
        timeout_seconds: float,
    ) -> GeminiHTTPResult:
        captured.update(
            endpoint=endpoint,
            body=json.loads(body),
            headers=headers,
            timeout=timeout_seconds,
        )
        return GeminiHTTPResult(
            payload=response_payload(),
            request_id="request_123",
        )

    result = run_gemini_generate_content(
        document(),
        capsule(),
        invocation(),
        api_key="test-secret",
        transport=fake_transport,
    )

    assert result.accepted
    assert result.provider_request_id == "request_123"
    assert captured["headers"] == {
        "x-goog-api-key": "test-secret",
        "content-type": "application/json",
        "accept": "application/json",
        "user-agent": f"e2h-gemini-runtime/{VERSION}",
    }
    record = result.archive.records[0]
    assert record.request_id == "request_123"
    assert record.model == "gemini-test-fast"
    assert [item.role for item in record.contents] == [
        "user",
        "model",
        "user",
    ]
    assert record.contents[0].id == "runtime-001.input.0"
    assert record.system_instruction is not None
    assert record.system_instruction.role == "system"
    assert record.candidate_ids == ["runtime-001.candidate.0"]
    assert result.archive.metadata["request_sha256"] == result.request.request_sha256
    assert "test-secret" not in result.model_dump_json()


def test_run_records_required_tool_policy_violation() -> None:
    required = document(tool_selection="required")

    def no_call(
        endpoint: str,
        body: bytes,
        headers: object,
        timeout_seconds: float,
    ) -> GeminiHTTPResult:
        del endpoint, body, headers, timeout_seconds
        return GeminiHTTPResult(payload=response_payload(tool_name=None))

    result = run_gemini_generate_content(
        required,
        capsule(),
        invocation(),
        api_key="key",
        transport=no_call,
    )
    assert not result.accepted
    assert result.policy_violations == [
        "provider returned no function call despite selection='required'"
    ]


def test_tool_policy_flags_unknown_parallel_and_server_tool_calls() -> None:
    payload = response_payload(tool_name="other")
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    candidate = candidates[0]
    assert isinstance(candidate, dict)
    content = candidate["content"]
    assert isinstance(content, dict)
    parts = content["parts"]
    assert isinstance(parts, list)
    parts.extend(
        [
            {
                "functionCall": {
                    "id": "call_2",
                    "name": "lookup",
                    "args": {},
                }
            },
            {"toolCall": {"id": "server_1", "toolType": "search"}},
        ]
    )
    tools = document(tool_selection="auto").variant.tools
    assert tools is not None
    violations = runtime._tool_policy_violations(tools, payload)
    assert "provider called undeclared tools: other" in violations
    assert "provider returned 2 function calls; max_calls is 1" in violations
    assert "provider returned parallel function calls despite parallel_calls=false" in violations
    assert "provider returned undeclared server-side tool calls" in violations


def test_tool_policy_validates_malformed_named_none_and_missing_candidates() -> None:
    malformed = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "id": "",
                                "name": "",
                                "args": "bad",
                            }
                        }
                    ],
                }
            }
        ]
    }
    none_tools = document(tool_selection="none").variant.tools
    assert none_tools is not None
    violations = runtime._tool_policy_violations(none_tools, malformed)
    assert "provider function call 0 has invalid id" in violations
    assert "provider function call 0 has invalid name" in violations
    assert "provider function call 0 args is not an object" in violations
    assert "provider returned function calls despite selection='none'" in violations

    named_tools = document(tool_selection="named").variant.tools
    assert named_tools is not None
    named = runtime._tool_policy_violations(
        named_tools,
        response_payload(tool_name="other"),
    )
    assert "provider called tools outside selected_tool 'lookup': other" in named

    assert runtime._tool_policy_violations(None, response_payload()) == [
        "provider returned function calls with no declared tools"
    ]
    assert runtime._tool_policy_violations(
        none_tools,
        {"candidates": "bad"},
    ) == ["provider response candidates is not an array"]


def test_invocation_request_endpoint_and_archive_helpers_fail_closed() -> None:
    with pytest.raises(ValueError, match="variable keys"):
        GeminiGenerateContentInvocation(
            id="runtime",
            variables={"bad-key": "value"},
        )
    with pytest.raises(ValueError, match="NUL"):
        GeminiGenerateContentInvocation(
            id="runtime",
            variables={"key": "bad\x00value"},
        )
    with pytest.raises(ValueError, match="non-empty"):
        GeminiGenerateContentInvocation(
            id="runtime",
            route_metadata={"tier": ""},
        )
    with pytest.raises(ValueError, match="65536"):
        GeminiGenerateContentInvocation(
            id="runtime",
            metadata={"large": "x" * 70_000},
        )
    with pytest.raises(GeminiRuntimeError, match="non-empty and trimmed"):
        runtime._endpoint("   ")

    with pytest.raises(ValueError, match="request_sha256"):
        GeminiGenerateContentRequest(
            invocation_id="runtime",
            variant_id="variant",
            variant_sha256="1" * 64,
            variant_document_sha256="2" * 64,
            base_capsule_sha256="3" * 64,
            route_target_id="route",
            model="gemini-test",
            endpoint="https://example.test",
            body={"contents": []},
            request_sha256="0" * 64,
        )

    assert runtime._build_tools(None) == ([], None)
    empty = ToolVariant.model_validate({"id": "empty", "tools": [], "selection": "none"})
    assert runtime._build_tools(empty) == ([], None)

    with pytest.raises(GeminiRuntimeError, match="contents must be an array"):
        runtime._archive_contents(
            SimpleNamespace(
                body={"contents": "bad"},
                invocation_id="run",
            )
        )
    with pytest.raises(GeminiRuntimeError, match="contain objects"):
        runtime._archive_contents(SimpleNamespace(body={"contents": [1]}, invocation_id="run"))
    with pytest.raises(GeminiRuntimeError, match="user/model role"):
        runtime._archive_contents(
            SimpleNamespace(
                body={"contents": [{"role": "system", "parts": []}]},
                invocation_id="run",
            )
        )
    with pytest.raises(GeminiRuntimeError, match="systemInstruction"):
        runtime._archive_system(
            SimpleNamespace(
                body={"systemInstruction": "bad"},
                invocation_id="run",
            )
        )
    with pytest.raises(GeminiRuntimeError, match="candidates must be an array"):
        runtime._candidate_ids("run", {"candidates": "bad"})


def test_http_transport_success_and_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, raw: bytes) -> None:
            self.raw = raw
            self.headers = {"x-goog-request-id": "request_123"}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return self.raw

    payload = response_payload(tool_name=None)
    monkeypatch.setattr(
        runtime,
        "urlopen",
        lambda request, timeout: Response(json.dumps(payload).encode("utf-8")),
    )
    result = runtime._http_transport("https://example.test", b"{}", {}, 1.0)
    assert result.payload == payload
    assert result.request_id == "request_123"

    monkeypatch.setattr(
        runtime,
        "urlopen",
        lambda request, timeout: Response(b"not-json"),
    )
    with pytest.raises(GeminiRuntimeError, match="not valid JSON"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)

    monkeypatch.setattr(
        runtime,
        "urlopen",
        lambda request, timeout: Response(b"[]"),
    )
    with pytest.raises(GeminiRuntimeError, match="JSON object"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)

    monkeypatch.setattr(runtime, "_MAX_RESPONSE_BYTES", 4)
    monkeypatch.setattr(
        runtime,
        "urlopen",
        lambda request, timeout: Response(b"12345"),
    )
    with pytest.raises(GeminiRuntimeError, match="response exceeds"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)

    error = HTTPError(
        "https://example.test",
        429,
        "rate",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"rate"}'),
    )

    def raise_http(request: object, timeout: float) -> object:
        raise error

    monkeypatch.setattr(runtime, "urlopen", raise_http)
    with pytest.raises(GeminiRuntimeError, match="HTTP 429"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)

    def raise_url(request: object, timeout: float) -> object:
        raise URLError("offline")

    monkeypatch.setattr(runtime, "urlopen", raise_url)
    with pytest.raises(GeminiRuntimeError, match="request failed"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)


def test_format_http_error_and_request_digest_are_deterministic() -> None:
    message = runtime._format_http_error(500, b"x" * 3_000)
    assert message.endswith("...")
    assert len(message) < 2_100
    assert runtime._format_http_error(500, b"") == (
        "Gemini GenerateContent request failed with HTTP 500"
    )

    body = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
    digest = hashlib.sha256(
        runtime._canonical_json_bytes({"model": "gemini-test", "body": body})
    ).hexdigest()
    request = GeminiGenerateContentRequest(
        invocation_id="runtime",
        variant_id="variant",
        variant_sha256="1" * 64,
        variant_document_sha256="2" * 64,
        base_capsule_sha256="3" * 64,
        route_target_id="route",
        model="gemini-test",
        endpoint="https://example.test",
        body=body,
        request_sha256=digest,
    )
    assert request.request_sha256 == digest


def test_runtime_rejects_invalid_key_and_invalid_provider_payload() -> None:
    with pytest.raises(GeminiRuntimeError, match="not header-safe"):
        run_gemini_generate_content(
            document(),
            capsule(),
            invocation(),
            api_key="bad\nkey",
        )

    def invalid_payload(
        endpoint: str,
        body: bytes,
        headers: object,
        timeout_seconds: float,
    ) -> GeminiHTTPResult:
        del endpoint, body, headers, timeout_seconds
        return GeminiHTTPResult(payload={"candidates": []})

    with pytest.raises(
        GeminiRuntimeError,
        match="invalid Gemini GenerateContent payload",
    ):
        run_gemini_generate_content(
            document(),
            capsule(),
            invocation(),
            api_key="key",
            transport=invalid_payload,
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
    loaded = load_gemini_generate_content_invocation(path)
    assert loaded.id == "runtime-002"
    assert loaded.max_output_tokens == 512

    path.write_text("id: bad\nunknown: true\n", encoding="utf-8")
    with pytest.raises(
        GeminiRuntimeError,
        match="invalid Gemini GenerateContent invocation",
    ):
        load_gemini_generate_content_invocation(path)
