from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.anthropic_runtime import AnthropicMessagesInvocation, AnthropicMessagesRequest
from e2h.gemini_runtime import GeminiGenerateContentInvocation, GeminiGenerateContentRequest
from e2h.openai_runtime import OpenAIResponsesInvocation, OpenAIResponsesRequest

SHA = "a" * 64


def _json_sha256(value: Any) -> str:
    rendered = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


@pytest.mark.parametrize(
    "invocation_type",
    [
        OpenAIResponsesInvocation,
        AnthropicMessagesInvocation,
        GeminiGenerateContentInvocation,
    ],
)
@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_provider_runtime_invocations_reject_json_coercible_metadata(
    invocation_type: type[Any],
    metadata: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        invocation_type(id="invocation", metadata=metadata)


def test_openai_request_rejects_json_coercible_body() -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        OpenAIResponsesRequest(
            invocation_id="invocation",
            variant_id="variant",
            variant_sha256=SHA,
            variant_document_sha256=SHA,
            base_capsule_sha256=SHA,
            route_target_id="route",
            model="model",
            body={"nested": (1, 2)},
            request_sha256=SHA,
        )


def test_anthropic_request_rejects_json_coercible_body() -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        AnthropicMessagesRequest(
            invocation_id="invocation",
            variant_id="variant",
            variant_sha256=SHA,
            variant_document_sha256=SHA,
            base_capsule_sha256=SHA,
            route_target_id="route",
            model="model",
            body={"nested": {1: "coerced key"}},
            request_sha256=SHA,
        )


def test_gemini_request_rejects_json_coercible_body() -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        GeminiGenerateContentRequest(
            invocation_id="invocation",
            variant_id="variant",
            variant_sha256=SHA,
            variant_document_sha256=SHA,
            base_capsule_sha256=SHA,
            route_target_id="route",
            model="model",
            endpoint="https://example.invalid/model",
            body={"nested": (1, 2)},
            request_sha256=SHA,
        )


def test_valid_provider_requests_preserve_canonical_digests() -> None:
    body = {"nested": [1, 2], "enabled": True}
    openai = OpenAIResponsesRequest(
        invocation_id="invocation",
        variant_id="variant",
        variant_sha256=SHA,
        variant_document_sha256=SHA,
        base_capsule_sha256=SHA,
        route_target_id="route",
        model="model",
        body=body,
        request_sha256=_json_sha256(body),
    )
    anthropic = AnthropicMessagesRequest(
        invocation_id="invocation",
        variant_id="variant",
        variant_sha256=SHA,
        variant_document_sha256=SHA,
        base_capsule_sha256=SHA,
        route_target_id="route",
        model="model",
        body=body,
        request_sha256=_json_sha256(body),
    )
    gemini_payload = {"model": "model", "body": body}
    gemini = GeminiGenerateContentRequest(
        invocation_id="invocation",
        variant_id="variant",
        variant_sha256=SHA,
        variant_document_sha256=SHA,
        base_capsule_sha256=SHA,
        route_target_id="route",
        model="model",
        endpoint="https://example.invalid/model",
        body=body,
        request_sha256=_json_sha256(gemini_payload),
    )

    assert openai.body == body
    assert anthropic.body == body
    assert gemini.body == body
