"""Normalize archived Gemini GenerateContent payloads into observable E2H traces."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.ingest import (
    EvidenceFormat,
    EvidenceIngestError,
    IngestionBundle,
    SourceProvenance,
    _load_json,
)
from e2h.privacy import RedactionPolicy, RedactionPolicyError, apply_redaction_policy
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

_MAX_PROVIDER_ITEMS = 10_000
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,255}$"
_BINARY_KEYS = frozenset({"thought_signature", "thoughtSignature"})
_PART_FIELDS = (
    "text",
    "function_call",
    "functionCall",
    "function_response",
    "functionResponse",
    "tool_call",
    "toolCall",
    "tool_response",
    "toolResponse",
    "executable_code",
    "executableCode",
    "code_execution_result",
    "codeExecutionResult",
    "inline_data",
    "inlineData",
    "file_data",
    "fileData",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _ensure_json(value: Any, noun: str) -> None:
    try:
        json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{noun} must contain canonical JSON values") from exc


def _mapping(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceIngestError(f"{location} must be an object")
    return cast(dict[str, Any], value)


def _list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceIngestError(f"{location} must be an array")
    return value


def _pick(mapping: dict[str, Any], snake: str, camel: str | None = None) -> Any:
    if snake in mapping:
        return mapping[snake]
    return mapping.get(camel) if camel is not None else None


def _safe(value: Any) -> Any:
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _safe(item) for key, item in value.items() if str(key) not in _BINARY_KEYS
        }
    return value


def _parts(value: Any, location: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(_list(value, location)):
        part = _mapping(item, f"{location}/{index}")
        populated = [field for field in _PART_FIELDS if part.get(field) is not None]
        thought = bool(part.get("thought", False))
        if not populated and not thought:
            raise EvidenceIngestError(f"{location}/{index} must contain an observable part field")
        result.append(part)
    return result


def _signature_part(part: dict[str, Any]) -> dict[str, Any]:
    descriptor = _part_descriptor(part)
    kind = descriptor["type"]
    if kind == "thought":
        return descriptor
    if kind == "text":
        return {"type": "text", "text": part.get("text")}
    if kind == "function_call":
        call = cast(dict[str, Any], _pick(part, "function_call", "functionCall"))
        return {
            **descriptor,
            "args": _safe(call.get("args", {})),
        }
    if kind == "function_response":
        response = cast(
            dict[str, Any],
            _pick(part, "function_response", "functionResponse"),
        )
        return {
            **descriptor,
            "response": _safe(response.get("response")),
            "parts": _safe(response.get("parts")),
            "will_continue": response.get("will_continue", response.get("willContinue")),
            "scheduling": response.get("scheduling"),
        }
    if kind == "tool_call":
        call = cast(dict[str, Any], _pick(part, "tool_call", "toolCall"))
        return {**descriptor, "args": _safe(call.get("args", {}))}
    if kind == "tool_response":
        response = cast(dict[str, Any], _pick(part, "tool_response", "toolResponse"))
        return {
            **descriptor,
            "response": _safe(response.get("response")),
        }
    if kind == "executable_code":
        code = cast(dict[str, Any], _pick(part, "executable_code", "executableCode"))
        return {
            **descriptor,
            "code": code.get("code"),
        }
    if kind == "code_execution_result":
        result = cast(
            dict[str, Any],
            _pick(part, "code_execution_result", "codeExecutionResult"),
        )
        return {
            **descriptor,
            "output": result.get("output"),
        }
    return descriptor


def _content_signature(role: str, parts: list[dict[str, Any]]) -> str:
    payload = {"role": role, "parts": [_signature_part(part) for part in parts]}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _part_descriptor(part: dict[str, Any]) -> dict[str, Any]:
    thought = bool(part.get("thought", False))
    signature_present = bool(_pick(part, "thought_signature", "thoughtSignature"))
    if thought:
        return {
            "type": "thought",
            "text_present": isinstance(part.get("text"), str) and bool(part.get("text")),
            "thought_signature_present": signature_present,
        }
    if isinstance(part.get("text"), str):
        return {"type": "text"}
    function_call = _pick(part, "function_call", "functionCall")
    if isinstance(function_call, dict):
        return {
            "type": "function_call",
            "id": function_call.get("id"),
            "name": function_call.get("name"),
        }
    function_response = _pick(part, "function_response", "functionResponse")
    if isinstance(function_response, dict):
        return {
            "type": "function_response",
            "id": function_response.get("id"),
            "name": function_response.get("name"),
        }
    tool_call = _pick(part, "tool_call", "toolCall")
    if isinstance(tool_call, dict):
        return {
            "type": "tool_call",
            "id": tool_call.get("id"),
            "tool_type": tool_call.get("tool_type", tool_call.get("toolType")),
        }
    tool_response = _pick(part, "tool_response", "toolResponse")
    if isinstance(tool_response, dict):
        return {
            "type": "tool_response",
            "id": tool_response.get("id"),
            "tool_type": tool_response.get("tool_type", tool_response.get("toolType")),
        }
    executable = _pick(part, "executable_code", "executableCode")
    if isinstance(executable, dict):
        return {
            "type": "executable_code",
            "id": executable.get("id"),
            "language": executable.get("language"),
        }
    result = _pick(part, "code_execution_result", "codeExecutionResult")
    if isinstance(result, dict):
        return {
            "type": "code_execution_result",
            "id": result.get("id"),
            "outcome": result.get("outcome"),
        }
    inline_data = _pick(part, "inline_data", "inlineData")
    if isinstance(inline_data, dict):
        return {
            "type": "inline_data",
            "mime_type": inline_data.get("mime_type", inline_data.get("mimeType")),
        }
    file_data = _pick(part, "file_data", "fileData")
    if isinstance(file_data, dict):
        return {
            "type": "file_data",
            "mime_type": file_data.get("mime_type", file_data.get("mimeType")),
            "file_uri": file_data.get("file_uri", file_data.get("fileUri")),
        }
    return {"type": "unknown", "metadata": _safe(part)}


def _render_parts(parts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    rendered: list[str] = []
    descriptors: list[dict[str, Any]] = []
    for part in parts:
        descriptor = _part_descriptor(part)
        descriptors.append(descriptor)
        kind = descriptor["type"]
        if kind == "thought":
            rendered.append("[thought]")
        elif kind == "text":
            rendered.append(cast(str, part["text"]))
        elif kind == "function_call":
            rendered.append(f"[function_call:{descriptor.get('name') or 'unknown'}]")
        elif kind == "function_response":
            rendered.append(f"[function_response:{descriptor.get('name') or 'unknown'}]")
        elif kind in {"tool_call", "tool_response", "executable_code", "code_execution_result"}:
            rendered.append(f"[{kind}]")
        elif kind in {"inline_data", "file_data"}:
            rendered.append(f"[{kind}:{descriptor.get('mime_type') or 'unknown'}]")
        else:
            rendered.append("[provider_part]")
    return "\n".join(rendered), descriptors


class GeminiContentRecord(StrictModel):
    """One archived request content with an exporter-assigned stable ID."""

    id: str = Field(pattern=_ID_PATTERN)
    role: Literal["user", "model", "system"]
    parts: list[dict[str, Any]] = Field(min_length=1, max_length=_MAX_PROVIDER_ITEMS)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def content_must_be_valid(self) -> GeminiContentRecord:
        _parts(self.parts, "content.parts")
        _ensure_json(self.parts, "content parts")
        _ensure_json(self.metadata, "content metadata")
        return self


class GeminiGenerateContentRecord(StrictModel):
    """One archived request context plus a raw GenerateContent response."""

    timestamp: datetime
    response: dict[str, Any]
    contents: list[GeminiContentRecord] = Field(
        default_factory=list, max_length=_MAX_PROVIDER_ITEMS
    )
    candidate_ids: list[str] = Field(default_factory=list, max_length=_MAX_PROVIDER_ITEMS)
    system_instruction: GeminiContentRecord | None = None
    request_id: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def response_must_have_core_fields(self) -> GeminiGenerateContentRecord:
        response_id = _pick(self.response, "response_id", "responseId")
        if not isinstance(response_id, str) or not response_id:
            raise ValueError("response.response_id must be a non-empty string")
        candidates = self.response.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError("response.candidates must be an array")
        if len(candidates) > _MAX_PROVIDER_ITEMS:
            raise ValueError(f"response.candidates exceed {_MAX_PROVIDER_ITEMS}")
        if len(self.candidate_ids) != len(candidates):
            raise ValueError("candidate_ids must align with response candidates")
        if len(set(self.candidate_ids)) != len(self.candidate_ids):
            raise ValueError("candidate_ids must be unique within a record")
        for index, raw_candidate in enumerate(candidates):
            candidate = _mapping(raw_candidate, f"response.candidates/{index}")
            content = candidate.get("content")
            if content is not None:
                content_mapping = _mapping(content, f"response.candidates/{index}/content")
                role = content_mapping.get("role", "model")
                if role != "model":
                    raise ValueError("candidate content role must be 'model'")
                _parts(content_mapping.get("parts"), f"response.candidates/{index}/content/parts")
        usage = _pick(self.response, "usage_metadata", "usageMetadata")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("response.usage_metadata must be an object")
        _ensure_json(self.response, "response")
        _ensure_json(self.metadata, "record metadata")
        return self


class GeminiGenerateContentDocument(StrictModel):
    """Portable archive envelope for one Gemini GenerateContent conversation."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    capsule_id: str = Field(default="unassigned", min_length=1, max_length=255)
    records: list[GeminiGenerateContentRecord] = Field(min_length=1, max_length=_MAX_PROVIDER_ITEMS)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def records_must_be_ordered_and_consistent(self) -> GeminiGenerateContentDocument:
        response_ids: set[str] = set()
        content_signatures: dict[str, str] = {}
        call_signatures: dict[str, str] = {}
        previous_timestamp: datetime | None = None
        item_count = 0

        def register_function_calls(parts: list[dict[str, Any]]) -> None:
            for part in parts:
                call = _pick(part, "function_call", "functionCall")
                if not isinstance(call, dict):
                    continue
                call_id = call.get("id")
                name = call.get("name")
                args = call.get("args", {})
                if not isinstance(call_id, str) or not call_id:
                    raise ValueError("function call id must be a non-empty string")
                if not isinstance(name, str) or not name:
                    raise ValueError("function call name must be a non-empty string")
                if not isinstance(args, dict):
                    raise ValueError("function call args must be an object")
                signature = json.dumps(
                    {"name": name, "args": args},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                existing = call_signatures.get(call_id)
                if existing is not None and existing != signature:
                    raise ValueError("function call ids must retain identical definitions")
                call_signatures[call_id] = signature

        def register_content(
            content_id: str,
            role: str,
            parts: list[dict[str, Any]],
            conflict_message: str,
        ) -> None:
            nonlocal item_count
            item_count += len(parts)
            register_function_calls(parts)
            signature = _content_signature(role, parts)
            existing = content_signatures.get(content_id)
            if existing is not None and existing != signature:
                raise ValueError(conflict_message)
            content_signatures[content_id] = signature

        for record in self.records:
            response_id = cast(
                str,
                _pick(record.response, "response_id", "responseId"),
            )
            if response_id in response_ids:
                raise ValueError("response ids must be unique")
            response_ids.add(response_id)
            if previous_timestamp is not None and record.timestamp < previous_timestamp:
                raise ValueError("record timestamps must be nondecreasing")
            previous_timestamp = record.timestamp

            request_contents = list(record.contents)
            if record.system_instruction is not None:
                request_contents.append(record.system_instruction)
            for request_content in request_contents:
                register_content(
                    request_content.id,
                    request_content.role,
                    request_content.parts,
                    "content ids must retain identical observable content",
                )

            for candidate_index, raw_candidate in enumerate(record.response["candidates"]):
                candidate = cast(dict[str, Any], raw_candidate)
                candidate_content = candidate.get("content")
                if not isinstance(candidate_content, dict):
                    continue
                candidate_parts = _parts(
                    candidate_content.get("parts"),
                    "candidate.content.parts",
                )
                register_content(
                    record.candidate_ids[candidate_index],
                    "model",
                    candidate_parts,
                    "candidate ids must retain identical observable content",
                )

            if item_count > _MAX_PROVIDER_ITEMS:
                raise ValueError(f"document exceeds {_MAX_PROVIDER_ITEMS} provider items")

        _ensure_json(self.metadata, "document metadata")
        return self


def _call_index(document: GeminiGenerateContentDocument) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in document.records:
        part_lists = [content.parts for content in record.contents]
        if record.system_instruction is not None:
            part_lists.append(record.system_instruction.parts)
        for candidate in record.response["candidates"]:
            if isinstance(candidate, dict) and isinstance(candidate.get("content"), dict):
                part_lists.append(
                    _parts(candidate["content"].get("parts"), "candidate.content.parts")
                )
        for parts in part_lists:
            for part in parts:
                for snake, camel in (
                    ("function_call", "functionCall"),
                    ("tool_call", "toolCall"),
                    ("executable_code", "executableCode"),
                ):
                    call = _pick(part, snake, camel)
                    if isinstance(call, dict) and isinstance(call.get("id"), str):
                        index[call["id"]] = {"kind": snake, **call}
    return index


def _append_part_events(
    events: list[TraceEvent],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    message_id: str,
    parts: list[dict[str, Any]],
    calls: dict[str, dict[str, Any]],
    origin: str,
) -> None:
    for part_index, part in enumerate(parts):
        descriptor = _part_descriptor(part)
        kind = descriptor["type"]
        if kind == "thought":
            events.append(
                TraceEvent(
                    trace_id=trace_id,
                    sequence=len(events),
                    event_type=TraceEventType.ARTIFACT_OBSERVED,
                    timestamp=timestamp,
                    context=context,
                    attributes={
                        "artifact_kind": "gemini.thought_presence",
                        "provider": "google",
                        "message_id": message_id,
                        "part_index": part_index,
                    },
                    payload=descriptor,
                )
            )
        elif kind in {"function_call", "tool_call", "executable_code"}:
            raw = _pick(
                part,
                kind,
                {
                    "function_call": "functionCall",
                    "tool_call": "toolCall",
                    "executable_code": "executableCode",
                }[kind],
            )
            call = cast(dict[str, Any], raw)
            events.append(
                TraceEvent(
                    trace_id=trace_id,
                    sequence=len(events),
                    event_type=TraceEventType.TOOL_CALLED,
                    timestamp=timestamp,
                    context=context,
                    attributes={
                        "provider": "google",
                        "call_id": call.get("id"),
                        "tool_name": call.get("name")
                        or call.get("tool_type")
                        or call.get("toolType")
                        or "code_execution",
                        "call_kind": kind,
                        "message_id": message_id,
                        "origin": origin,
                    },
                    payload={
                        "args": _safe(call.get("args", {})),
                        "code": call.get("code"),
                        "language": call.get("language"),
                    },
                )
            )
        elif kind in {"function_response", "tool_response", "code_execution_result"}:
            raw = _pick(
                part,
                kind,
                {
                    "function_response": "functionResponse",
                    "tool_response": "toolResponse",
                    "code_execution_result": "codeExecutionResult",
                }[kind],
            )
            response = cast(dict[str, Any], raw)
            call_id = response.get("id")
            linked = calls.get(call_id) if isinstance(call_id, str) else None
            events.append(
                TraceEvent(
                    trace_id=trace_id,
                    sequence=len(events),
                    event_type=TraceEventType.TOOL_COMPLETED,
                    timestamp=timestamp,
                    context=context,
                    attributes={
                        "provider": "google",
                        "call_id": call_id,
                        "tool_name": response.get("name")
                        or (linked.get("name") if linked else None)
                        or (linked.get("tool_type") if linked else None),
                        "linked_call": linked is not None,
                        "result_kind": kind,
                        "message_id": message_id,
                        "origin": origin,
                        "outcome": response.get("outcome"),
                    },
                    payload={
                        "response": _safe(response.get("response")),
                        "output": response.get("output"),
                        "parts": _safe(response.get("parts")),
                    },
                )
            )
        elif kind not in {"text"}:
            events.append(
                TraceEvent(
                    trace_id=trace_id,
                    sequence=len(events),
                    event_type=TraceEventType.ARTIFACT_OBSERVED,
                    timestamp=timestamp,
                    context=context,
                    attributes={
                        "artifact_kind": "gemini.part",
                        "provider": "google",
                        "part_type": kind,
                        "message_id": message_id,
                        "part_index": part_index,
                        "origin": origin,
                    },
                    payload=descriptor,
                )
            )


def _append_message(
    events: list[TraceEvent],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    message_id: str,
    role: str,
    parts: list[dict[str, Any]],
    metadata: dict[str, Any],
    calls: dict[str, dict[str, Any]],
    origin: str,
    request_id: str | None,
) -> None:
    rendered, descriptors = _render_parts(parts)
    events.append(
        TraceEvent(
            trace_id=trace_id,
            sequence=len(events),
            event_type=TraceEventType.MESSAGE_OBSERVED,
            timestamp=timestamp,
            context=context,
            attributes={
                "provider": "google",
                "message_id": message_id,
                "role": role,
                "origin": origin,
                "request_id": request_id,
            },
            payload={"content": rendered, "parts": descriptors, "metadata": metadata},
        )
    )
    _append_part_events(
        events,
        trace_id=trace_id,
        context=context,
        timestamp=timestamp,
        message_id=message_id,
        parts=parts,
        calls=calls,
        origin=origin,
    )


def import_gemini_generate_content_document(
    document: GeminiGenerateContentDocument,
    provenance: SourceProvenance,
    *,
    capsule_id: str | None = None,
    redaction_policy: RedactionPolicy | None = None,
) -> IngestionBundle:
    """Normalize a validated Gemini archive into observable trace events."""
    selected_capsule = capsule_id or document.capsule_id
    context = TraceContext(
        run_id=document.id,
        capsule_id=selected_capsule,
        metadata={
            **document.metadata,
            "source_format": EvidenceFormat.GEMINI_GENERATE_CONTENT_JSON.value,
            "provider": "google",
        },
    )
    events = [
        TraceEvent(
            trace_id=document.id,
            sequence=0,
            event_type=TraceEventType.CONVERSATION_STARTED,
            timestamp=document.records[0].timestamp,
            context=context,
            payload={"conversation_id": document.id, "record_count": len(document.records)},
        )
    ]
    calls = _call_index(document)
    seen: dict[str, str] = {}
    for record_index, record in enumerate(document.records):
        inputs = (
            [record.system_instruction] if record.system_instruction is not None else []
        ) + list(record.contents)
        for content in inputs:
            signature = _content_signature(content.role, content.parts)
            if content.id in seen:
                continue
            seen[content.id] = signature
            _append_message(
                events,
                trace_id=document.id,
                context=context,
                timestamp=record.timestamp,
                message_id=content.id,
                role=content.role,
                parts=content.parts,
                metadata=content.metadata,
                calls=calls,
                origin="request",
                request_id=record.request_id,
            )
        response_id = cast(str, _pick(record.response, "response_id", "responseId"))
        for candidate_position, raw_candidate in enumerate(record.response["candidates"]):
            candidate = cast(dict[str, Any], raw_candidate)
            candidate_content = candidate.get("content")
            if isinstance(candidate_content, dict):
                parts = _parts(candidate_content.get("parts"), "candidate.content.parts")
                message_id = record.candidate_ids[candidate_position]
                signature = _content_signature("model", parts)
                if message_id not in seen:
                    seen[message_id] = signature
                    _append_message(
                        events,
                        trace_id=document.id,
                        context=context,
                        timestamp=record.timestamp,
                        message_id=message_id,
                        role="model",
                        parts=parts,
                        metadata={},
                        calls=calls,
                        origin="response",
                        request_id=record.request_id,
                    )
            events.append(
                TraceEvent(
                    trace_id=document.id,
                    sequence=len(events),
                    event_type=TraceEventType.ARTIFACT_OBSERVED,
                    timestamp=record.timestamp,
                    context=context,
                    attributes={
                        "artifact_kind": "gemini.candidate",
                        "provider": "google",
                        "response_id": response_id,
                        "candidate_index": candidate.get("index", candidate_position),
                    },
                    payload={
                        "finish_reason": candidate.get(
                            "finish_reason", candidate.get("finishReason")
                        ),
                        "finish_message": candidate.get(
                            "finish_message", candidate.get("finishMessage")
                        ),
                        "token_count": candidate.get("token_count", candidate.get("tokenCount")),
                        "citation_metadata": _safe(
                            _pick(candidate, "citation_metadata", "citationMetadata")
                        ),
                        "grounding_metadata": _safe(
                            _pick(candidate, "grounding_metadata", "groundingMetadata")
                        ),
                        "safety_ratings": _safe(
                            _pick(candidate, "safety_ratings", "safetyRatings")
                        ),
                        "url_context_metadata": _safe(
                            _pick(candidate, "url_context_metadata", "urlContextMetadata")
                        ),
                    },
                )
            )
        events.append(
            TraceEvent(
                trace_id=document.id,
                sequence=len(events),
                event_type=TraceEventType.ARTIFACT_OBSERVED,
                timestamp=record.timestamp,
                context=context,
                attributes={
                    "artifact_kind": "gemini.response",
                    "provider": "google",
                    "response_id": response_id,
                    "record_index": record_index,
                },
                payload={
                    "model": record.model,
                    "model_version": _pick(record.response, "model_version", "modelVersion"),
                    "create_time": _pick(record.response, "create_time", "createTime"),
                    "usage_metadata": _safe(
                        _pick(record.response, "usage_metadata", "usageMetadata")
                    ),
                    "prompt_feedback": _safe(
                        _pick(record.response, "prompt_feedback", "promptFeedback")
                    ),
                    "model_status": _safe(_pick(record.response, "model_status", "modelStatus")),
                    "request_id": record.request_id,
                    "record_metadata": record.metadata,
                    "candidate_count": len(record.response["candidates"]),
                },
            )
        )
    events.append(
        TraceEvent(
            trace_id=document.id,
            sequence=len(events),
            event_type=TraceEventType.ARTIFACT_OBSERVED,
            timestamp=document.records[-1].timestamp,
            context=context,
            attributes={"artifact_kind": "gemini.conversation", "provider": "google"},
            payload={
                "record_count": len(document.records),
                "observable_content_count": len(seen),
                "call_count": len(calls),
            },
        )
    )
    try:
        outcome = apply_redaction_policy(
            [Trace(trace_id=document.id, events=events)],
            policy=redaction_policy,
            redaction_enabled=provenance.redaction_enabled,
            max_records=_MAX_PROVIDER_ITEMS,
        )
    except RedactionPolicyError as exc:
        raise EvidenceIngestError(str(exc)) from exc
    return IngestionBundle(
        provenance=provenance,
        traces=outcome.traces,
        corrections=[],
        redactions=outcome.records,
        redaction_review=outcome.review,
    )


def ingest_gemini_generate_content_file(
    path: Path,
    *,
    capsule_id: str | None = None,
    redact: bool = True,
    redaction_policy: RedactionPolicy | None = None,
) -> IngestionBundle:
    """Load and normalize an archived Gemini GenerateContent JSON export."""
    source = _load_json(path, EvidenceFormat.GEMINI_GENERATE_CONTENT_JSON, redact, redaction_policy)
    try:
        document = GeminiGenerateContentDocument.model_validate(source.data)
        return import_gemini_generate_content_document(
            document, source.provenance, capsule_id=capsule_id, redaction_policy=redaction_policy
        )
    except ValueError as exc:
        if isinstance(exc, EvidenceIngestError):
            raise
        raise EvidenceIngestError(str(exc)) from exc
