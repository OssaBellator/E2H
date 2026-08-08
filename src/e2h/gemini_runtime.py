"""Live Gemini GenerateContent execution bound to verified E2H typed variants."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import load_mapping_document
from e2h.gemini_generate_content import (
    GeminiContentRecord,
    GeminiGenerateContentDocument,
    GeminiGenerateContentRecord,
)
from e2h.models import TaskCapsule
from e2h.runtime_validation import revalidate_runtime_inputs
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

_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
_MAX_DOCUMENT_BYTES = 1_048_576
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_ERROR_BYTES = 65_536
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_VARIABLE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]{0,127})\}")


class GeminiRuntimeError(ValueError):
    """Raised when a live Gemini GenerateContent invocation cannot run safely."""


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


class GeminiGenerateContentInvocation(StrictModel):
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
    def metadata_must_be_bounded_json(self) -> GeminiGenerateContentInvocation:
        if len(_canonical_json_bytes(self.metadata)) > 65_536:
            raise ValueError("invocation metadata exceeds 65536 bytes")
        return self


class GeminiGenerateContentRequest(StrictModel):
    """Canonical Gemini request materialized from a verified E2H variant."""

    schema_version: Literal["0.1"] = "0.1"
    invocation_id: str = Field(pattern=_ID_PATTERN)
    variant_id: str = Field(pattern=_ID_PATTERN)
    variant_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_capsule_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_target_id: str = Field(pattern=_ID_PATTERN)
    model: str = Field(min_length=1, max_length=256)
    endpoint: str = Field(min_length=1, max_length=1024)
    body: dict[str, Any]
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def request_digest_must_match(self) -> GeminiGenerateContentRequest:
        payload = {"model": self.model, "body": self.body}
        expected = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
        if self.request_sha256 != expected:
            raise ValueError("request_sha256 does not match the canonical Gemini request")
        return self


class GeminiGenerateContentRuntimeResult(StrictModel):
    """One live Gemini result plus its observable archive."""

    schema_version: Literal["0.1"] = "0.1"
    request: GeminiGenerateContentRequest
    provider_request_id: str | None = Field(default=None, max_length=255)
    policy_violations: list[str] = Field(default_factory=list, max_length=128)
    archive: GeminiGenerateContentDocument

    @property
    def accepted(self) -> bool:
        """Return whether the response satisfied the local tool contract."""
        return not self.policy_violations


@dataclass(frozen=True)
class GeminiHTTPResult:
    """Decoded HTTP response returned by an injectable transport."""

    payload: dict[str, Any]
    request_id: str | None = None


GeminiTransport = Callable[[str, bytes, Mapping[str, str], float], GeminiHTTPResult]


def _select_route(routing: RoutingVariant, metadata: Mapping[str, str]) -> RouteTarget:
    targets = {target.id: target for target in routing.targets}
    matches = [
        rule
        for rule in routing.rules
        if all(metadata.get(key) == expected for key, expected in rule.match.items())
    ]
    if matches:
        target = targets[max(matches, key=lambda rule: rule.priority).target_id]
    else:
        target = targets[routing.fallback_target]
    if target.provider.casefold() not in {"google", "gemini"}:
        raise GeminiRuntimeError(
            f"selected routing target {target.id!r} uses provider "
            f"{target.provider!r}, not 'google' or 'gemini'"
        )
    return target


def _endpoint(model: str) -> str:
    model_id = model.removeprefix("models/")
    if not model_id or model_id != model_id.strip():
        raise GeminiRuntimeError("Gemini model id must be non-empty and trimmed")
    return f"{_API_ROOT}/{quote(model_id, safe='')}:generateContent"


def _render_prompt_message(message: PromptMessage, variables: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return variables[match.group(1)]

    return _PLACEHOLDER_PATTERN.sub(replace, message.content)


def _context_items(context: ContextVariant) -> list[LiteralContextItem]:
    literals: list[tuple[int, LiteralContextItem]] = []
    for index, item in enumerate(context.items):
        if not isinstance(item, LiteralContextItem):
            raise GeminiRuntimeError(
                f"context item {item.id!r} is referenced; the Gemini runtime does not "
                "dereference artifact, snapshot, or trace locators"
            )
        if item.placement == "tool_context":
            raise GeminiRuntimeError(
                f"context item {item.id!r} uses tool_context, which has no implicit Gemini mapping"
            )
        if item.placement == "after_prompt":
            raise GeminiRuntimeError(
                f"context item {item.id!r} uses after_prompt; Gemini GenerateContent has "
                "no faithful system-instruction placement after conversational contents"
            )
        literals.append((index, item))

    rendered = [(index, item, item.content[: item.max_chars]) for index, item in literals]
    if sum(len(content) for _, _, content in rendered) > context.max_chars:
        if context.overflow == "reject":
            raise GeminiRuntimeError("materialized context exceeds the declared max_chars")
        budget = context.max_chars
        kept: dict[str, str] = {}
        prioritized = sorted(rendered, key=lambda entry: (-entry[1].priority, entry[0]))
        for _, item, content in prioritized:
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
    invocation: GeminiGenerateContentInvocation,
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    variant = document.variant
    if variant.prompt is None:
        raise GeminiRuntimeError("Gemini runtime requires a prompt variant")
    declared = set(variant.prompt.variables)
    supplied = set(invocation.variables)
    missing = sorted(declared - supplied)
    extra = sorted(supplied - declared)
    if missing:
        raise GeminiRuntimeError(f"missing prompt variables: {', '.join(missing)}")
    if extra:
        raise GeminiRuntimeError(f"undeclared prompt variables supplied: {', '.join(extra)}")

    system_parts: list[dict[str, str]] = []
    if variant.context is not None:
        system_parts.extend({"text": item.content} for item in _context_items(variant.context))

    contents: list[dict[str, Any]] = []
    conversation_started = False
    for message in variant.prompt.messages:
        rendered = _render_prompt_message(message, invocation.variables)
        if message.role in {"system", "developer"}:
            if conversation_started:
                raise GeminiRuntimeError(
                    f"prompt message {message.id!r} uses role {message.role!r} after "
                    "conversational messages; Gemini systemInstruction is top-level only"
                )
            system_parts.append({"text": rendered})
            continue
        conversation_started = True
        role = "model" if message.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": rendered}]})

    if not contents:
        raise GeminiRuntimeError("Gemini runtime requires at least one user or assistant message")
    return system_parts, contents


def _build_tools(
    tools: ToolVariant | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if tools is None:
        return [], None
    declarations = [
        {
            "name": tool.id,
            "description": tool.description,
            "parameters": tool.input_schema,
        }
        for tool in tools.tools
    ]
    if not declarations:
        return [], None
    config: dict[str, Any] = {"mode": "AUTO"}
    if tools.selection == "none":
        config = {"mode": "NONE"}
    elif tools.selection == "required":
        config = {"mode": "ANY"}
    elif tools.selection == "named":
        config = {"mode": "ANY", "allowedFunctionNames": [tools.selected_tool]}
    return [{"functionDeclarations": declarations}], {"functionCallingConfig": config}


def build_gemini_generate_content_request(
    document: HarnessVariantDocument,
    capsule: TaskCapsule,
    invocation: GeminiGenerateContentInvocation,
) -> GeminiGenerateContentRequest:
    """Materialize a deterministic GenerateContent request from a verified variant."""
    document, capsule, invocation = revalidate_runtime_inputs(
        document,
        capsule,
        invocation,
        GeminiGenerateContentInvocation,
        error_type=GeminiRuntimeError,
        invocation_noun='Gemini GenerateContent invocation',
    )
    try:
        verification = verify_variant_document(document, capsule)
    except VariantError as exc:
        raise GeminiRuntimeError(str(exc)) from exc
    variant = document.variant
    if variant.routing is None:
        raise GeminiRuntimeError("Gemini runtime requires a routing variant")
    if variant.workflow is not None:
        raise GeminiRuntimeError(
            "Gemini runtime does not execute workflow DAGs; materialize one model turn explicitly"
        )

    target = _select_route(variant.routing, invocation.route_metadata)
    system_parts, contents = _build_prompt(document, invocation)
    tools, tool_config = _build_tools(variant.tools)
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": invocation.max_output_tokens},
        "store": False,
    }
    if system_parts:
        body["systemInstruction"] = {"parts": system_parts}
    if tools:
        body["tools"] = tools
        body["toolConfig"] = tool_config

    request_payload = {"model": target.model, "body": body}
    request_sha256 = hashlib.sha256(_canonical_json_bytes(request_payload)).hexdigest()
    return GeminiGenerateContentRequest(
        invocation_id=invocation.id,
        variant_id=variant.id,
        variant_sha256=verification.variant_sha256,
        variant_document_sha256=variant_document_sha256(document),
        base_capsule_sha256=verification.base_capsule_sha256,
        route_target_id=target.id,
        model=target.model,
        endpoint=_endpoint(target.model),
        body=body,
        request_sha256=request_sha256,
    )


def _archive_contents(
    request: GeminiGenerateContentRequest,
) -> list[GeminiContentRecord]:
    raw_contents = request.body.get("contents")
    if not isinstance(raw_contents, list):
        raise GeminiRuntimeError("materialized Gemini contents must be an array")
    contents: list[GeminiContentRecord] = []
    for index, raw_content in enumerate(raw_contents):
        if not isinstance(raw_content, dict):
            raise GeminiRuntimeError("materialized Gemini contents must contain objects")
        role = raw_content.get("role")
        parts = raw_content.get("parts")
        if role not in {"user", "model"} or not isinstance(parts, list):
            raise GeminiRuntimeError(
                "materialized Gemini content requires user/model role and parts"
            )
        contents.append(
            GeminiContentRecord(
                id=f"{request.invocation_id}.input.{index}",
                role=cast(Literal["user", "model"], role),
                parts=cast(list[dict[str, Any]], parts),
            )
        )
    return contents


def _archive_system(
    request: GeminiGenerateContentRequest,
) -> GeminiContentRecord | None:
    raw_system = request.body.get("systemInstruction")
    if raw_system is None:
        return None
    if not isinstance(raw_system, dict) or not isinstance(raw_system.get("parts"), list):
        raise GeminiRuntimeError("materialized Gemini systemInstruction must contain parts")
    return GeminiContentRecord(
        id=f"{request.invocation_id}.system",
        role="system",
        parts=cast(list[dict[str, Any]], raw_system["parts"]),
    )


def _candidate_ids(invocation_id: str, response: Mapping[str, Any]) -> list[str]:
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        raise GeminiRuntimeError("Gemini response candidates must be an array")
    return [f"{invocation_id}.candidate.{index}" for index in range(len(candidates))]


def _function_calls(response: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        return [], False
    calls: list[dict[str, Any]] = []
    unexpected_tool_call = False
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            call = part.get("functionCall")
            if isinstance(call, dict):
                calls.append(cast(dict[str, Any], call))
            if part.get("toolCall") is not None or part.get("executableCode") is not None:
                unexpected_tool_call = True
    return calls, unexpected_tool_call


def _tool_policy_violations(
    tools: ToolVariant | None,
    response: Mapping[str, Any],
) -> list[str]:
    if not isinstance(response.get("candidates"), list):
        return ["provider response candidates is not an array"]
    calls, unexpected_tool_call = _function_calls(response)
    violations: list[str] = []
    if unexpected_tool_call:
        violations.append("provider returned undeclared server-side tool calls")
    if tools is None:
        if calls:
            violations.append("provider returned function calls with no declared tools")
        return violations

    declared = {tool.id for tool in tools.tools}
    names: list[str] = []
    for index, call in enumerate(calls):
        call_id = call.get("id")
        if call_id is not None and (not isinstance(call_id, str) or not call_id):
            violations.append(f"provider function call {index} has invalid id")
        name = call.get("name")
        if not isinstance(name, str) or not name:
            violations.append(f"provider function call {index} has invalid name")
        else:
            names.append(name)
        if not isinstance(call.get("args", {}), dict):
            violations.append(f"provider function call {index} args is not an object")

    unknown = sorted({name for name in names if name not in declared})
    if unknown:
        violations.append(f"provider called undeclared tools: {', '.join(unknown)}")
    if len(calls) > tools.max_calls:
        count = len(calls)
        violations.append(
            f"provider returned {count} function calls; max_calls is {tools.max_calls}"
        )
    if not tools.parallel_calls and len(calls) > 1:
        violations.append("provider returned parallel function calls despite parallel_calls=false")
    if tools.selection == "none" and calls:
        violations.append("provider returned function calls despite selection='none'")
    if tools.selection == "required" and not calls:
        violations.append("provider returned no function call despite selection='required'")
    if tools.selection == "named":
        if len(calls) != 1:
            violations.append(
                f"provider returned {len(calls)} function calls; selection='named' "
                "requires exactly one"
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
        return f"Gemini GenerateContent request failed with HTTP {status}: {detail}"
    return f"Gemini GenerateContent request failed with HTTP {status}"


def _http_transport(
    endpoint: str,
    body: bytes,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> GeminiHTTPResult:
    request = Request(endpoint, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            request_id = response.headers.get("x-goog-request-id") or response.headers.get(
                "x-request-id"
            )
    except HTTPError as exc:
        raw_error = exc.read(_MAX_ERROR_BYTES)
        raise GeminiRuntimeError(_format_http_error(exc.code, raw_error)) from exc
    except (URLError, OSError) as exc:
        raise GeminiRuntimeError(f"Gemini GenerateContent request failed: {exc}") from exc
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise GeminiRuntimeError(
            f"Gemini GenerateContent response exceeds {_MAX_RESPONSE_BYTES} bytes"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GeminiRuntimeError("Gemini GenerateContent response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise GeminiRuntimeError("Gemini GenerateContent response must be a JSON object")
    return GeminiHTTPResult(
        payload=cast(dict[str, Any], payload),
        request_id=request_id,
    )


def run_gemini_generate_content(
    document: HarnessVariantDocument,
    capsule: TaskCapsule,
    invocation: GeminiGenerateContentInvocation,
    *,
    api_key: str,
    transport: GeminiTransport | None = None,
) -> GeminiGenerateContentRuntimeResult:
    """Execute one live GenerateContent request and preserve an observable archive."""
    if not api_key or any(character in api_key for character in "\r\n\x00"):
        raise GeminiRuntimeError("Gemini API key is missing or not header-safe")
    document, capsule, invocation = revalidate_runtime_inputs(
        document,
        capsule,
        invocation,
        GeminiGenerateContentInvocation,
        error_type=GeminiRuntimeError,
        invocation_noun='Gemini GenerateContent invocation',
    )
    request = build_gemini_generate_content_request(document, capsule, invocation)
    sender = transport or _http_transport
    http_result = sender(
        request.endpoint,
        _canonical_json_bytes(request.body),
        {
            "x-goog-api-key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": "e2h-gemini-runtime/0.27",
        },
        invocation.timeout_seconds,
    )
    candidate_ids = _candidate_ids(invocation.id, http_result.payload)
    try:
        record = GeminiGenerateContentRecord(
            timestamp=datetime.now(UTC),
            response=http_result.payload,
            contents=_archive_contents(request),
            candidate_ids=candidate_ids,
            system_instruction=_archive_system(request),
            request_id=http_result.request_id,
            model=request.model,
            metadata={
                "request_sha256": request.request_sha256,
                "variant_document_sha256": request.variant_document_sha256,
                "variant_sha256": request.variant_sha256,
                "route_target_id": request.route_target_id,
            },
        )
    except ValueError as exc:
        raise GeminiRuntimeError(f"invalid Gemini GenerateContent payload: {exc}") from exc

    violations = _tool_policy_violations(document.variant.tools, http_result.payload)
    archive = GeminiGenerateContentDocument(
        id=invocation.id,
        capsule_id=capsule.id,
        records=[record],
        metadata={
            **invocation.metadata,
            "runtime": "gemini-generate-content",
            "request_sha256": request.request_sha256,
            "variant_document_sha256": request.variant_document_sha256,
            "variant_sha256": request.variant_sha256,
            "route_target_id": request.route_target_id,
            "tool_policy_violations": violations,
        },
    )
    return GeminiGenerateContentRuntimeResult(
        request=request,
        provider_request_id=http_result.request_id,
        policy_violations=violations,
        archive=archive,
    )


def load_gemini_generate_content_invocation(path: Any) -> GeminiGenerateContentInvocation:
    """Load one strict JSON/YAML Gemini runtime invocation document."""
    try:
        payload = load_mapping_document(
            path,
            noun="Gemini GenerateContent invocation",
            max_bytes=_MAX_DOCUMENT_BYTES,
        )
        return GeminiGenerateContentInvocation.model_validate(payload)
    except ValueError as exc:
        if isinstance(exc, GeminiRuntimeError):
            raise
        raise GeminiRuntimeError(f"invalid Gemini GenerateContent invocation: {exc}") from exc
