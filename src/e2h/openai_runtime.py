"""Live OpenAI Responses execution bound to verified E2H typed variants."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import load_mapping_document
from e2h.models import TaskCapsule
from e2h.openai_responses import OpenAIResponseRecord, OpenAIResponsesDocument
from e2h.variants import (
    ContextVariant,
    HarnessVariantDocument,
    LiteralContextItem,
    PromptMessage,
    RouteTarget,
    RoutingVariant,
    ToolVariant,
    VariantError,
    variant_document_sha256,
    verify_variant_document,
)

_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
_MAX_DOCUMENT_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_ERROR_BYTES = 65_536
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_VARIABLE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]{0,127})\}")


class OpenAIRuntimeError(ValueError):
    """Raised when a live Responses invocation cannot be built or executed safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must contain canonical JSON data") from exc
    return rendered.encode("utf-8")


def _safe_text(value: str, *, noun: str) -> str:
    if "\x00" in value:
        raise ValueError(f"{noun} must not contain NUL")
    return value


class OpenAIResponsesInvocation(StrictModel):
    """Runtime-only inputs that fill one verified typed variant."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    variables: dict[str, str] = Field(default_factory=dict, max_length=128)
    route_metadata: dict[str, str] = Field(default_factory=dict, max_length=64)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("variables")
    @classmethod
    def variables_must_be_safe(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if _VARIABLE_PATTERN.fullmatch(key) is None:
                raise ValueError("invocation variable keys must be identifiers")
            _safe_text(item, noun="invocation variable")
        return value

    @field_validator("route_metadata")
    @classmethod
    def route_metadata_must_be_safe(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if _VARIABLE_PATTERN.fullmatch(key) is None:
                raise ValueError("route metadata keys must be identifiers")
            if not item:
                raise ValueError("route metadata values must be non-empty")
            _safe_text(item, noun="route metadata value")
        return value

    @model_validator(mode="after")
    def metadata_must_be_bounded_json(self) -> OpenAIResponsesInvocation:
        if len(_canonical_json_bytes(self.metadata)) > 65_536:
            raise ValueError("invocation metadata exceeds 65536 bytes")
        return self


class OpenAIResponsesRequest(StrictModel):
    """Canonical request materialized from a verified E2H variant."""

    schema_version: Literal["0.1"] = "0.1"
    invocation_id: str = Field(pattern=_ID_PATTERN)
    variant_id: str = Field(pattern=_ID_PATTERN)
    variant_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_capsule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_target_id: str = Field(pattern=_ID_PATTERN)
    model: str = Field(min_length=1, max_length=256)
    body: dict[str, Any]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def request_digest_must_match_body(self) -> OpenAIResponsesRequest:
        expected = hashlib.sha256(_canonical_json_bytes(self.body)).hexdigest()
        if self.request_sha256 != expected:
            raise ValueError("request_sha256 does not match the canonical request body")
        return self


class OpenAIResponsesRuntimeResult(StrictModel):
    """One live provider result plus the archive used by the existing ingestion adapter."""

    schema_version: Literal["0.1"] = "0.1"
    request: OpenAIResponsesRequest
    provider_request_id: str | None = Field(default=None, max_length=255)
    policy_violations: list[str] = Field(default_factory=list, max_length=128)
    archive: OpenAIResponsesDocument

    @property
    def accepted(self) -> bool:
        """Return whether the response satisfied the local typed-tool contract."""
        return not self.policy_violations


@dataclass(frozen=True)
class OpenAIHTTPResult:
    """Decoded HTTP response returned by an injectable transport."""

    payload: dict[str, Any]
    request_id: str | None = None


OpenAITransport = Callable[[str, bytes, Mapping[str, str], float], OpenAIHTTPResult]


def _select_route(routing: RoutingVariant, metadata: Mapping[str, str]) -> RouteTarget:
    targets = {target.id: target for target in routing.targets}
    matches = [
        rule
        for rule in routing.rules
        if all(metadata.get(key) == expected for key, expected in rule.match.items())
    ]
    if matches:
        selected_rule = max(matches, key=lambda rule: rule.priority)
        target = targets[selected_rule.target_id]
    else:
        target = targets[routing.fallback_target]
    if target.provider.casefold() != "openai":
        raise OpenAIRuntimeError(
            f"selected routing target {target.id!r} uses provider {target.provider!r}, not 'openai'"
        )
    return target


def _render_prompt_message(message: PromptMessage, variables: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return variables[match.group(1)]

    return _PLACEHOLDER_PATTERN.sub(replace, message.content)


def _context_items(context: ContextVariant) -> list[LiteralContextItem]:
    literals: list[tuple[int, LiteralContextItem]] = []
    for index, item in enumerate(context.items):
        if not isinstance(item, LiteralContextItem):
            raise OpenAIRuntimeError(
                f"context item {item.id!r} is referenced; the OpenAI runtime does not dereference "
                "artifact, snapshot, or trace locators"
            )
        if item.placement == "tool_context":
            raise OpenAIRuntimeError(
                f"context item {item.id!r} uses tool_context, which has no implicit OpenAI mapping"
            )
        literals.append((index, item))

    rendered = [(index, item, item.content[: item.max_chars]) for index, item in literals]
    total = sum(len(content) for _, _, content in rendered)
    if total > context.max_chars:
        if context.overflow == "reject":
            raise OpenAIRuntimeError("materialized context exceeds the declared max_chars")
        budget = context.max_chars
        kept: dict[str, str] = {}
        for _, item, content in sorted(rendered, key=lambda entry: (-entry[1].priority, entry[0])):
            if budget <= 0:
                break
            selected = content[:budget]
            if selected:
                kept[item.id] = selected
                budget -= len(selected)
        rendered = [(index, item, kept[item.id]) for index, item, _ in rendered if item.id in kept]

    if context.ordering == "priority":
        rendered.sort(key=lambda entry: (-entry[1].priority, entry[0]))
    return [
        item.model_copy(update={"content": content, "max_chars": len(content)})
        for _, item, content in rendered
    ]


def _build_messages(
    document: HarnessVariantDocument,
    invocation: OpenAIResponsesInvocation,
) -> list[dict[str, Any]]:
    variant = document.variant
    if variant.prompt is None:
        raise OpenAIRuntimeError("OpenAI runtime requires a prompt variant")
    declared = set(variant.prompt.variables)
    supplied = set(invocation.variables)
    missing = sorted(declared - supplied)
    extra = sorted(supplied - declared)
    if missing:
        raise OpenAIRuntimeError(f"missing prompt variables: {', '.join(missing)}")
    if extra:
        raise OpenAIRuntimeError(f"undeclared prompt variables supplied: {', '.join(extra)}")

    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    if variant.context is not None:
        for item in _context_items(variant.context):
            target = before if item.placement == "before_prompt" else after
            target.append({"role": "developer", "content": item.content})

    prompt = [
        {
            "role": message.role,
            "content": _render_prompt_message(message, invocation.variables),
        }
        for message in variant.prompt.messages
    ]
    return [*before, *prompt, *after]


def _build_tools(
    tools: ToolVariant | None,
) -> tuple[list[dict[str, Any]], Any | None, bool | None]:
    if tools is None:
        return [], None, None
    rendered = [
        {
            "type": "function",
            "name": tool.id,
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        for tool in tools.tools
    ]
    if tools.selection == "named":
        choice: Any = {"type": "function", "name": tools.selected_tool}
    else:
        choice = tools.selection
    return rendered, choice, tools.parallel_calls


def build_openai_responses_request(
    document: HarnessVariantDocument,
    capsule: TaskCapsule,
    invocation: OpenAIResponsesInvocation,
) -> OpenAIResponsesRequest:
    """Materialize one deterministic Responses request from a verified typed variant."""
    try:
        verification = verify_variant_document(document, capsule)
    except VariantError as exc:
        raise OpenAIRuntimeError(str(exc)) from exc
    variant = document.variant
    if variant.routing is None:
        raise OpenAIRuntimeError("OpenAI runtime requires a routing variant")
    if variant.workflow is not None:
        raise OpenAIRuntimeError(
            "OpenAI runtime does not execute workflow DAGs; materialize one model turn explicitly"
        )
    target = _select_route(variant.routing, invocation.route_metadata)
    messages = _build_messages(document, invocation)
    tools, tool_choice, parallel_tool_calls = _build_tools(variant.tools)

    body: dict[str, Any] = {
        "model": target.model,
        "input": messages,
        "store": False,
        "metadata": {
            "e2h_invocation_id": invocation.id,
            "e2h_variant_id": variant.id,
            "e2h_variant_sha256": verification.variant_sha256,
            "e2h_capsule_id": capsule.id,
        },
    }
    if invocation.max_output_tokens is not None:
        body["max_output_tokens"] = invocation.max_output_tokens
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice
        body["parallel_tool_calls"] = parallel_tool_calls

    request_sha256 = hashlib.sha256(_canonical_json_bytes(body)).hexdigest()
    return OpenAIResponsesRequest(
        invocation_id=invocation.id,
        variant_id=variant.id,
        variant_sha256=verification.variant_sha256,
        variant_document_sha256=variant_document_sha256(document),
        base_capsule_sha256=verification.base_capsule_sha256,
        route_target_id=target.id,
        model=target.model,
        body=body,
        request_sha256=request_sha256,
    )


def _archive_input_items(request: OpenAIResponsesRequest) -> list[dict[str, Any]]:
    raw_input = request.body.get("input")
    if not isinstance(raw_input, list):
        raise OpenAIRuntimeError("materialized request input must be an array")
    items: list[dict[str, Any]] = []
    for index, raw_message in enumerate(raw_input):
        if not isinstance(raw_message, dict):
            raise OpenAIRuntimeError("materialized request messages must be objects")
        role = raw_message.get("role")
        content = raw_message.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise OpenAIRuntimeError(
                "materialized request messages require string role and content"
            )
        items.append(
            {
                "id": f"{request.invocation_id}.input.{index}",
                "type": "message",
                "role": role,
                "content": [{"type": "input_text", "text": content}],
                "status": "completed",
            }
        )
    return items


def _tool_policy_violations(
    tools: ToolVariant | None,
    response: Mapping[str, Any],
) -> list[str]:
    raw_output = response.get("output")
    if not isinstance(raw_output, list):
        return ["provider response output is not an array"]
    calls = [
        item
        for item in raw_output
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]
    if tools is None:
        if calls:
            return ["provider returned tool calls with no declared tools"]
        return []

    declared = {tool.id for tool in tools.tools}
    names: list[str] = []
    violations: list[str] = []
    for index, call in enumerate(calls):
        call_id = call.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            violations.append(f"provider function call {index} has invalid call_id")

        name = call.get("name")
        if not isinstance(name, str) or not name:
            violations.append(f"provider function call {index} has invalid name")
        else:
            names.append(name)

        arguments = call.get("arguments")
        if not isinstance(arguments, str):
            violations.append(f"provider function call {index} arguments are not a string")
        else:
            try:
                decoded_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                violations.append(f"provider function call {index} arguments are not valid JSON")
            else:
                if not isinstance(decoded_arguments, dict):
                    violations.append(
                        f"provider function call {index} arguments must decode to an object"
                    )

    unknown = sorted({name for name in names if name not in declared})
    if unknown:
        violations.append(f"provider called undeclared tools: {', '.join(unknown)}")
    if len(calls) > tools.max_calls:
        violations.append(
            f"provider returned {len(calls)} tool calls; max_calls is {tools.max_calls}"
        )
    if tools.selection == "none" and calls:
        violations.append("provider returned tool calls despite selection='none'")
    if tools.selection == "required" and not calls:
        violations.append("provider returned no tool call despite selection='required'")
    if tools.selection == "named":
        if len(calls) != 1:
            violations.append(
                f"provider returned {len(calls)} tool calls despite selection='named' requiring exactly one"
            )
        wrong = sorted({name for name in names if name != tools.selected_tool})
        if wrong:
            violations.append(
                f"provider called tools outside selected_tool {tools.selected_tool!r}: "
                f"{', '.join(wrong)}"
            )
    return violations


def _format_http_error(status: int, raw: bytes) -> str:
    detail = raw.decode("utf-8", errors="replace").strip()
    if len(detail) > 2_000:
        detail = detail[:2_000] + "..."
    if detail:
        return f"OpenAI Responses request failed with HTTP {status}: {detail}"
    return f"OpenAI Responses request failed with HTTP {status}"


def _http_transport(
    endpoint: str,
    body: bytes,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> OpenAIHTTPResult:
    request = Request(endpoint, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            request_id = response.headers.get("x-request-id")
    except HTTPError as exc:
        raw_error = exc.read(_MAX_ERROR_BYTES)
        raise OpenAIRuntimeError(_format_http_error(exc.code, raw_error)) from exc
    except (URLError, OSError) as exc:
        raise OpenAIRuntimeError(f"OpenAI Responses request failed: {exc}") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise OpenAIRuntimeError(f"OpenAI Responses response exceeds {_MAX_RESPONSE_BYTES} bytes")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpenAIRuntimeError("OpenAI Responses response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise OpenAIRuntimeError("OpenAI Responses response must be a JSON object")
    return OpenAIHTTPResult(payload=cast(dict[str, Any], payload), request_id=request_id)


def run_openai_responses(
    document: HarnessVariantDocument,
    capsule: TaskCapsule,
    invocation: OpenAIResponsesInvocation,
    *,
    api_key: str,
    transport: OpenAITransport | None = None,
) -> OpenAIResponsesRuntimeResult:
    """Execute one live Responses request and preserve a replayable observable archive."""
    if not api_key or any(character in api_key for character in "\r\n\x00"):
        raise OpenAIRuntimeError("OpenAI API key is missing or not header-safe")
    request = build_openai_responses_request(document, capsule, invocation)
    sender = transport or _http_transport
    http_result = sender(
        _RESPONSES_ENDPOINT,
        _canonical_json_bytes(request.body),
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "e2h-openai-runtime/0.18",
        },
        invocation.timeout_seconds,
    )
    try:
        record = OpenAIResponseRecord(
            response=http_result.payload,
            input_items=_archive_input_items(request),
            request_id=http_result.request_id,
        )
    except ValueError as exc:
        raise OpenAIRuntimeError(f"invalid OpenAI Responses payload: {exc}") from exc

    violations = _tool_policy_violations(document.variant.tools, http_result.payload)
    archive = OpenAIResponsesDocument(
        id=invocation.id,
        capsule_id=capsule.id,
        responses=[record],
        metadata={
            **invocation.metadata,
            "runtime": "openai-responses",
            "request_sha256": request.request_sha256,
            "variant_document_sha256": request.variant_document_sha256,
            "variant_sha256": request.variant_sha256,
            "route_target_id": request.route_target_id,
            "tool_policy_violations": violations,
        },
    )
    return OpenAIResponsesRuntimeResult(
        request=request,
        provider_request_id=http_result.request_id,
        policy_violations=violations,
        archive=archive,
    )


def load_openai_responses_invocation(path: Any) -> OpenAIResponsesInvocation:
    """Load one strict JSON/YAML runtime invocation document."""
    try:
        payload = load_mapping_document(
            path,
            noun="OpenAI Responses invocation",
            max_bytes=_MAX_DOCUMENT_BYTES,
        )
        return OpenAIResponsesInvocation.model_validate(payload)
    except ValueError as exc:
        if isinstance(exc, OpenAIRuntimeError):
            raise
        raise OpenAIRuntimeError(f"invalid OpenAI Responses invocation: {exc}") from exc
