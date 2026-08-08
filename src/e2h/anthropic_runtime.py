"""Live Anthropic Messages execution bound to verified E2H typed variants."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.anthropic_messages import (
    AnthropicInputMessage,
    AnthropicMessageRecord,
    AnthropicMessagesDocument,
)
from e2h.document import load_mapping_document
from e2h.models import TaskCapsule
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

_MESSAGES_ENDPOINT = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"
_MAX_DOCUMENT_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_ERROR_BYTES = 65_536
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_VARIABLE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]{0,127})\}")


class AnthropicRuntimeError(ValueError):
    """Raised when a live Anthropic Messages invocation cannot be built or executed safely."""


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


class AnthropicMessagesInvocation(StrictModel):
    """Runtime-only inputs that fill one verified typed variant."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    variables: dict[str, str] = Field(default_factory=dict, max_length=128)
    route_metadata: dict[str, str] = Field(default_factory=dict, max_length=64)
    max_output_tokens: int = Field(default=1024, ge=1, le=1_000_000)
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
    def metadata_must_be_bounded_json(self) -> AnthropicMessagesInvocation:
        if len(_canonical_json_bytes(self.metadata)) > 65_536:
            raise ValueError("invocation metadata exceeds 65536 bytes")
        return self


class AnthropicMessagesRequest(StrictModel):
    """Canonical Anthropic request materialized from a verified E2H variant."""

    schema_version: Literal["0.1"] = "0.1"
    invocation_id: str = Field(pattern=_ID_PATTERN)
    variant_id: str = Field(pattern=_ID_PATTERN)
    variant_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_capsule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_target_id: str = Field(pattern=_ID_PATTERN)
    model: str = Field(min_length=1, max_length=256)
    anthropic_version: Literal["2023-06-01"] = _ANTHROPIC_VERSION
    body: dict[str, Any]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def request_digest_must_match_body(self) -> AnthropicMessagesRequest:
        expected = hashlib.sha256(_canonical_json_bytes(self.body)).hexdigest()
        if self.request_sha256 != expected:
            raise ValueError("request_sha256 does not match the canonical request body")
        return self


class AnthropicMessagesRuntimeResult(StrictModel):
    """One live Anthropic result plus the archive used by the ingestion adapter."""

    schema_version: Literal["0.1"] = "0.1"
    request: AnthropicMessagesRequest
    provider_request_id: str | None = Field(default=None, max_length=255)
    policy_violations: list[str] = Field(default_factory=list, max_length=128)
    archive: AnthropicMessagesDocument

    @property
    def accepted(self) -> bool:
        """Return whether the response satisfied the local typed-tool contract."""
        return not self.policy_violations


@dataclass(frozen=True)
class AnthropicHTTPResult:
    """Decoded HTTP response returned by an injectable transport."""

    payload: dict[str, Any]
    request_id: str | None = None


AnthropicTransport = Callable[[str, bytes, Mapping[str, str], float], AnthropicHTTPResult]


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
    if target.provider.casefold() != "anthropic":
        raise AnthropicRuntimeError(
            f"selected routing target {target.id!r} uses provider {target.provider!r}, "
            "not 'anthropic'"
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
            raise AnthropicRuntimeError(
                f"context item {item.id!r} is referenced; the Anthropic runtime does not "
                "dereference artifact, snapshot, or trace locators"
            )
        if item.placement == "tool_context":
            raise AnthropicRuntimeError(
                f"context item {item.id!r} uses tool_context, which has no implicit "
                "Anthropic mapping"
            )
        if item.placement == "after_prompt":
            raise AnthropicRuntimeError(
                f"context item {item.id!r} uses after_prompt; Anthropic Messages has no "
                "faithful developer-message placement after conversational messages"
            )
        literals.append((index, item))

    rendered = [(index, item, item.content[: item.max_chars]) for index, item in literals]
    total = sum(len(content) for _, _, content in rendered)
    if total > context.max_chars:
        if context.overflow == "reject":
            raise AnthropicRuntimeError("materialized context exceeds the declared max_chars")
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


def _build_prompt(
    document: HarnessVariantDocument,
    invocation: AnthropicMessagesInvocation,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    variant = document.variant
    if variant.prompt is None:
        raise AnthropicRuntimeError("Anthropic runtime requires a prompt variant")
    declared = set(variant.prompt.variables)
    supplied = set(invocation.variables)
    missing = sorted(declared - supplied)
    extra = sorted(supplied - declared)
    if missing:
        raise AnthropicRuntimeError(f"missing prompt variables: {', '.join(missing)}")
    if extra:
        raise AnthropicRuntimeError(f"undeclared prompt variables supplied: {', '.join(extra)}")

    system_blocks: list[dict[str, str]] = []
    if variant.context is not None:
        system_blocks.extend(
            {"type": "text", "text": item.content} for item in _context_items(variant.context)
        )

    messages: list[dict[str, str]] = []
    conversation_started = False
    for message in variant.prompt.messages:
        rendered = _render_prompt_message(message, invocation.variables)
        if message.role in {"system", "developer"}:
            if conversation_started:
                raise AnthropicRuntimeError(
                    f"prompt message {message.id!r} uses role {message.role!r} after "
                    "conversational messages; Anthropic system content is top-level only"
                )
            system_blocks.append({"type": "text", "text": rendered})
            continue
        conversation_started = True
        messages.append({"role": message.role, "content": rendered})

    if not messages:
        raise AnthropicRuntimeError(
            "Anthropic runtime requires at least one user or assistant prompt message"
        )
    return system_blocks, messages


def _build_tools(
    tools: ToolVariant | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if tools is None:
        return [], None
    rendered = [
        {
            "name": tool.id,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }
        for tool in tools.tools
    ]
    if not rendered:
        return [], None

    disable_parallel = not tools.parallel_calls
    if tools.selection == "named":
        choice: dict[str, Any] = {
            "type": "tool",
            "name": tools.selected_tool,
            "disable_parallel_tool_use": disable_parallel,
        }
    elif tools.selection == "required":
        choice = {"type": "any", "disable_parallel_tool_use": disable_parallel}
    elif tools.selection == "auto":
        choice = {"type": "auto", "disable_parallel_tool_use": disable_parallel}
    else:
        choice = {"type": "none"}
    return rendered, choice


def build_anthropic_messages_request(
    document: HarnessVariantDocument,
    capsule: TaskCapsule,
    invocation: AnthropicMessagesInvocation,
) -> AnthropicMessagesRequest:
    """Materialize one deterministic Anthropic Messages request from a verified variant."""
    try:
        verification = verify_variant_document(document, capsule)
    except VariantError as exc:
        raise AnthropicRuntimeError(str(exc)) from exc
    variant = document.variant
    if variant.routing is None:
        raise AnthropicRuntimeError("Anthropic runtime requires a routing variant")
    if variant.workflow is not None:
        raise AnthropicRuntimeError(
            "Anthropic runtime does not execute workflow DAGs; materialize one model turn explicitly"
        )

    target = _select_route(variant.routing, invocation.route_metadata)
    system_blocks, messages = _build_prompt(document, invocation)
    tools, tool_choice = _build_tools(variant.tools)

    body: dict[str, Any] = {
        "model": target.model,
        "max_tokens": invocation.max_output_tokens,
        "messages": messages,
    }
    if system_blocks:
        body["system"] = system_blocks
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice

    request_sha256 = hashlib.sha256(_canonical_json_bytes(body)).hexdigest()
    return AnthropicMessagesRequest(
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


def _archive_messages(request: AnthropicMessagesRequest) -> list[AnthropicInputMessage]:
    raw_messages = request.body.get("messages")
    if not isinstance(raw_messages, list):
        raise AnthropicRuntimeError("materialized request messages must be an array")
    messages: list[AnthropicInputMessage] = []
    for index, raw_message in enumerate(raw_messages):
        if not isinstance(raw_message, dict):
            raise AnthropicRuntimeError("materialized request messages must be objects")
        role = raw_message.get("role")
        content = raw_message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            raise AnthropicRuntimeError(
                "materialized Anthropic messages require user/assistant role and string content"
            )
        messages.append(
            AnthropicInputMessage(
                id=f"{request.invocation_id}.input.{index}",
                role=cast(Literal["user", "assistant"], role),
                content=content,
            )
        )
    return messages


def _archive_system(request: AnthropicMessagesRequest) -> list[dict[str, Any]] | None:
    raw_system = request.body.get("system")
    if raw_system is None:
        return None
    if not isinstance(raw_system, list):
        raise AnthropicRuntimeError("materialized Anthropic system content must be an array")
    return cast(list[dict[str, Any]], raw_system)


def _tool_policy_violations(
    tools: ToolVariant | None,
    response: Mapping[str, Any],
) -> list[str]:
    raw_content = response.get("content")
    if not isinstance(raw_content, list):
        return ["provider response content is not an array"]
    calls = [
        block
        for block in raw_content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    if tools is None:
        if calls:
            return ["provider returned tool calls with no declared tools"]
        return []

    declared = {tool.id for tool in tools.tools}
    names: list[str] = []
    violations: list[str] = []
    for index, call in enumerate(calls):
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            violations.append(f"provider tool call {index} has invalid id")
        name = call.get("name")
        if not isinstance(name, str) or not name:
            violations.append(f"provider tool call {index} has invalid name")
        else:
            names.append(name)
        tool_input = call.get("input")
        if not isinstance(tool_input, dict):
            violations.append(f"provider tool call {index} input is not an object")

    unknown = sorted({name for name in names if name not in declared})
    if unknown:
        violations.append(f"provider called undeclared tools: {', '.join(unknown)}")
    if len(calls) > tools.max_calls:
        violations.append(
            f"provider returned {len(calls)} tool calls; max_calls is {tools.max_calls}"
        )
    if not tools.parallel_calls and len(calls) > 1:
        violations.append("provider returned parallel tool calls despite parallel_calls=false")
    if tools.selection == "none" and calls:
        violations.append("provider returned tool calls despite selection='none'")
    if tools.selection == "required" and not calls:
        violations.append("provider returned no tool call despite selection='required'")
    if tools.selection == "named":
        if len(calls) != 1:
            violations.append(
                f"provider returned {len(calls)} tool calls; selection='named' requires exactly one"
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
        return f"Anthropic Messages request failed with HTTP {status}: {detail}"
    return f"Anthropic Messages request failed with HTTP {status}"


def _http_transport(
    endpoint: str,
    body: bytes,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> AnthropicHTTPResult:
    request = Request(endpoint, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            request_id = response.headers.get("request-id")
    except HTTPError as exc:
        raw_error = exc.read(_MAX_ERROR_BYTES)
        raise AnthropicRuntimeError(_format_http_error(exc.code, raw_error)) from exc
    except (URLError, OSError) as exc:
        raise AnthropicRuntimeError(f"Anthropic Messages request failed: {exc}") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise AnthropicRuntimeError(
            f"Anthropic Messages response exceeds {_MAX_RESPONSE_BYTES} bytes"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnthropicRuntimeError("Anthropic Messages response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise AnthropicRuntimeError("Anthropic Messages response must be a JSON object")
    return AnthropicHTTPResult(payload=cast(dict[str, Any], payload), request_id=request_id)


def run_anthropic_messages(
    document: HarnessVariantDocument,
    capsule: TaskCapsule,
    invocation: AnthropicMessagesInvocation,
    *,
    api_key: str,
    transport: AnthropicTransport | None = None,
) -> AnthropicMessagesRuntimeResult:
    """Execute one live Messages request and preserve a replayable observable archive."""
    if not api_key or any(character in api_key for character in "\r\n\x00"):
        raise AnthropicRuntimeError("Anthropic API key is missing or not header-safe")
    request = build_anthropic_messages_request(document, capsule, invocation)
    sender = transport or _http_transport
    http_result = sender(
        _MESSAGES_ENDPOINT,
        _canonical_json_bytes(request.body),
        {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "e2h-anthropic-runtime/0.28",
        },
        invocation.timeout_seconds,
    )
    try:
        record = AnthropicMessageRecord(
            timestamp=datetime.now(timezone.utc),
            response=http_result.payload,
            messages=_archive_messages(request),
            system=_archive_system(request),
            request_id=http_result.request_id,
            metadata={
                "request_sha256": request.request_sha256,
                "variant_document_sha256": request.variant_document_sha256,
                "variant_sha256": request.variant_sha256,
                "route_target_id": request.route_target_id,
            },
        )
    except ValueError as exc:
        raise AnthropicRuntimeError(f"invalid Anthropic Messages payload: {exc}") from exc

    violations = _tool_policy_violations(document.variant.tools, http_result.payload)
    archive = AnthropicMessagesDocument(
        id=invocation.id,
        capsule_id=capsule.id,
        records=[record],
        metadata={
            **invocation.metadata,
            "runtime": "anthropic-messages",
            "anthropic_version": _ANTHROPIC_VERSION,
            "request_sha256": request.request_sha256,
            "variant_document_sha256": request.variant_document_sha256,
            "variant_sha256": request.variant_sha256,
            "route_target_id": request.route_target_id,
            "tool_policy_violations": violations,
        },
    )
    return AnthropicMessagesRuntimeResult(
        request=request,
        provider_request_id=http_result.request_id,
        policy_violations=violations,
        archive=archive,
    )


def load_anthropic_messages_invocation(path: Any) -> AnthropicMessagesInvocation:
    """Load one strict JSON/YAML Anthropic runtime invocation document."""
    try:
        payload = load_mapping_document(
            path,
            noun="Anthropic Messages invocation",
            max_bytes=_MAX_DOCUMENT_BYTES,
        )
        return AnthropicMessagesInvocation.model_validate(payload)
    except ValueError as exc:
        if isinstance(exc, AnthropicRuntimeError):
            raise
        raise AnthropicRuntimeError(f"invalid Anthropic Messages invocation: {exc}") from exc
