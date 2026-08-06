"""Normalize archived Anthropic Messages API payloads into observable E2H traces."""

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
from e2h.privacy import (
    RedactionPolicy,
    RedactionPolicyError,
    apply_redaction_policy,
)
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

_MAX_PROVIDER_ITEMS = 10_000
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,255}$"
_TOOL_CALL_TYPES = {"tool_use", "server_tool_use"}
_TOOL_RESULT_TYPES = {
    "tool_result",
    "web_search_tool_result",
    "web_fetch_tool_result",
    "code_execution_tool_result",
    "bash_code_execution_tool_result",
    "text_editor_code_execution_tool_result",
    "tool_search_tool_result",
}
_HIDDEN_THINKING_TYPES = {"thinking", "redacted_thinking"}
_PLACEHOLDER_TYPES = {
    "image": "[image]",
    "document": "[document]",
    "search_result": "[search_result]",
    "container_upload": "[container_upload]",
    "tool_reference": "[tool_reference]",
    "mid_conversation_system": "[system]",
}


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


def _content_list(value: Any, location: str) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    if not isinstance(value, list):
        raise EvidenceIngestError(f"{location} must be a string or array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        block = _mapping(item, f"{location}/{index}")
        block_type = block.get("type")
        if not isinstance(block_type, str) or not block_type:
            raise EvidenceIngestError(f"{location}/{index}/type must be a non-empty string")
        result.append(block)
    return result


def _provider_safe(value: Any, *, hidden_keys: frozenset[str] = frozenset()) -> Any:
    if isinstance(value, list):
        return [_provider_safe(item, hidden_keys=hidden_keys) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _provider_safe(item, hidden_keys=hidden_keys)
            for key, item in value.items()
            if str(key) not in hidden_keys and str(key) != "encrypted_content"
        }
    return value


def _block_descriptor(block: dict[str, Any]) -> dict[str, Any]:
    block_type = cast(str, block["type"])
    if block_type == "text":
        return {
            "type": block_type,
            "citations": _provider_safe(block.get("citations", [])),
        }
    if block_type in _HIDDEN_THINKING_TYPES:
        return {
            "type": block_type,
            "thinking_present": bool(block.get("thinking")),
            "signature_present": bool(block.get("signature")),
            "redacted_data_present": bool(block.get("data")),
        }
    if block_type in _TOOL_CALL_TYPES:
        return {
            "type": block_type,
            "id": block.get("id"),
            "name": block.get("name"),
            "caller": _provider_safe(block.get("caller")),
        }
    if block_type in _TOOL_RESULT_TYPES:
        return {
            "type": block_type,
            "tool_use_id": block.get("tool_use_id"),
            "is_error": block.get("is_error", False),
            "caller": _provider_safe(block.get("caller")),
        }
    if block_type in {"image", "document"}:
        source = block.get("source")
        source_metadata: dict[str, Any] = {}
        if isinstance(source, dict):
            for key in ("type", "media_type", "file_id", "url"):
                if key in source:
                    source_metadata[key] = source[key]
        return {"type": block_type, "source": source_metadata}
    if block_type == "search_result":
        return {
            "type": block_type,
            "title": block.get("title"),
            "source": block.get("source"),
            "citations": _provider_safe(block.get("citations", [])),
        }
    return {
        "type": block_type,
        "metadata": _provider_safe(
            block,
            hidden_keys=frozenset({"thinking", "signature", "data", "source"}),
        ),
    }


def _observable_content_signature(role: str, content: Any) -> str:
    blocks = _content_list(content, "message.content")
    payload = {
        "role": role,
        "blocks": [_block_descriptor(block) for block in blocks],
        "texts": [block.get("text") for block in blocks if block.get("type") == "text"],
        "tools": [
            {
                "type": block.get("type"),
                "id": block.get("id"),
                "name": block.get("name"),
                "input": _provider_safe(block.get("input")),
                "tool_use_id": block.get("tool_use_id"),
                "content": _provider_safe(block.get("content")),
                "is_error": block.get("is_error", False),
            }
            for block in blocks
            if block.get("type") in _TOOL_CALL_TYPES | _TOOL_RESULT_TYPES
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AnthropicInputMessage(StrictModel):
    """One archived request message with an exporter-assigned stable ID."""

    id: str = Field(pattern=_ID_PATTERN)
    role: Literal["user", "assistant", "system"]
    content: str | list[dict[str, Any]]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def content_must_be_typed_and_json(self) -> AnthropicInputMessage:
        _content_list(self.content, "message.content")
        _ensure_json(self.content, "message content")
        _ensure_json(self.metadata, "message metadata")
        return self


class AnthropicMessageRecord(StrictModel):
    """One archived request context plus the raw Anthropic response message."""

    timestamp: datetime
    response: dict[str, Any]
    messages: list[AnthropicInputMessage] = Field(
        default_factory=list,
        max_length=_MAX_PROVIDER_ITEMS,
    )
    system: str | list[dict[str, Any]] | None = None
    request_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def response_must_have_core_fields(self) -> AnthropicMessageRecord:
        response_id = self.response.get("id")
        if not isinstance(response_id, str) or not response_id:
            raise ValueError("response.id must be a non-empty string")
        if self.response.get("type") != "message":
            raise ValueError("response.type must be 'message'")
        if self.response.get("role") != "assistant":
            raise ValueError("response.role must be 'assistant'")
        model = self.response.get("model")
        if not isinstance(model, str) or not model:
            raise ValueError("response.model must be a non-empty string")
        content = self.response.get("content")
        blocks = _content_list(content, "response.content")
        if len(blocks) > _MAX_PROVIDER_ITEMS:
            raise ValueError(f"response.content exceeds {_MAX_PROVIDER_ITEMS} blocks")
        usage = self.response.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("response.usage must be an object")
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"response.usage.{key} must be a non-negative integer")
        if self.system is not None:
            _content_list(self.system, "system")
        _ensure_json(self.response, "response")
        _ensure_json(self.system, "system")
        _ensure_json(self.metadata, "record metadata")
        return self


class AnthropicMessagesDocument(StrictModel):
    """Portable archive envelope for one Anthropic Messages conversation."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    capsule_id: str = Field(default="unassigned", min_length=1, max_length=255)
    records: list[AnthropicMessageRecord] = Field(
        min_length=1,
        max_length=_MAX_PROVIDER_ITEMS,
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        _ensure_json(value, "document metadata")
        return value

    @model_validator(mode="after")
    def records_must_be_ordered_and_consistent(self) -> AnthropicMessagesDocument:
        response_ids: set[str] = set()
        observable_messages: dict[str, str] = {}
        tool_uses: dict[str, str] = {}
        previous_timestamp: datetime | None = None
        provider_items = 0
        for record in self.records:
            response_id = cast(str, record.response["id"])
            if response_id in response_ids:
                raise ValueError("response ids must be unique")
            response_ids.add(response_id)
            if previous_timestamp is not None and record.timestamp < previous_timestamp:
                raise ValueError("record timestamps must be nondecreasing")
            previous_timestamp = record.timestamp
            messages = list(record.messages)
            if record.system is not None:
                system_signature = _observable_content_signature("system", record.system)
                messages.append(
                    AnthropicInputMessage(
                        id=f"system:{system_signature[:32]}",
                        role="system",
                        content=record.system,
                    )
                )
            messages.append(
                AnthropicInputMessage(
                    id=response_id,
                    role="assistant",
                    content=cast(list[dict[str, Any]], record.response["content"]),
                )
            )
            for message in messages:
                blocks = _content_list(message.content, "message.content")
                provider_items += len(blocks)
                for block in blocks:
                    block_type = block.get("type")
                    if block_type not in _TOOL_CALL_TYPES:
                        continue
                    tool_id = block.get("id")
                    name = block.get("name")
                    tool_input = block.get("input")
                    if not isinstance(tool_id, str) or not tool_id:
                        raise ValueError("tool use id must be a non-empty string")
                    if not isinstance(name, str) or not name:
                        raise ValueError("tool use name must be a non-empty string")
                    if not isinstance(tool_input, dict):
                        raise ValueError("tool use input must be an object")
                    signature = json.dumps(
                        {
                            "type": block_type,
                            "name": name,
                            "input": tool_input,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    existing_tool = tool_uses.get(tool_id)
                    if existing_tool is not None and existing_tool != signature:
                        raise ValueError("tool use ids must retain identical definitions")
                    tool_uses[tool_id] = signature
                message_signature = _observable_content_signature(message.role, message.content)
                existing_message = observable_messages.get(message.id)
                if existing_message is not None and existing_message != message_signature:
                    raise ValueError("message ids must retain identical observable content")
                observable_messages[message.id] = message_signature
                if provider_items > _MAX_PROVIDER_ITEMS:
                    raise ValueError(f"document exceeds {_MAX_PROVIDER_ITEMS} provider items")
        return self


def _system_message(record: AnthropicMessageRecord) -> AnthropicInputMessage | None:
    if record.system is None:
        return None
    signature = _observable_content_signature("system", record.system)
    return AnthropicInputMessage(
        id=f"system:{signature[:32]}",
        role="system",
        content=record.system,
    )


def _tool_index(document: AnthropicMessagesDocument) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for record in document.records:
        messages = [*record.messages]
        system = _system_message(record)
        if system is not None:
            messages.append(system)
        messages.append(
            AnthropicInputMessage(
                id=cast(str, record.response["id"]),
                role="assistant",
                content=cast(list[dict[str, Any]], record.response["content"]),
            )
        )
        for message in messages:
            for block in _content_list(message.content, "message.content"):
                if block.get("type") in _TOOL_CALL_TYPES:
                    index[cast(str, block["id"])] = block
    return index


def _render_blocks(blocks: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    descriptors: list[dict[str, Any]] = []
    for block in blocks:
        block_type = cast(str, block["type"])
        descriptors.append(_block_descriptor(block))
        if block_type == "text":
            text = block.get("text")
            if not isinstance(text, str):
                raise EvidenceIngestError("text block text must be a string")
            text_parts.append(text)
        elif block_type in _HIDDEN_THINKING_TYPES:
            text_parts.append("[thinking]")
        elif block_type in _TOOL_CALL_TYPES:
            text_parts.append(f"[tool_use:{block.get('name', 'unknown')}]")
        elif block_type in _TOOL_RESULT_TYPES:
            text_parts.append(f"[tool_result:{block.get('tool_use_id', 'unknown')}]")
        else:
            text_parts.append(_PLACEHOLDER_TYPES.get(block_type, f"[{block_type}]"))
    return "\n".join(part for part in text_parts if part), descriptors


def _tool_result_content(block: dict[str, Any]) -> Any:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        rendered, descriptors = _render_blocks(_content_list(content, "tool_result.content"))
        return {"text": rendered, "blocks": descriptors}
    if content is None:
        return None
    return _provider_safe(content)


def _append_block_events(
    events: list[TraceEvent],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    message_id: str,
    blocks: list[dict[str, Any]],
    tool_index: dict[str, dict[str, Any]],
    origin: str,
) -> None:
    for block_index, block in enumerate(blocks):
        block_type = cast(str, block["type"])
        if block_type in _HIDDEN_THINKING_TYPES:
            events.append(
                TraceEvent(
                    trace_id=trace_id,
                    sequence=len(events),
                    event_type=TraceEventType.ARTIFACT_OBSERVED,
                    timestamp=timestamp,
                    context=context,
                    attributes={
                        "artifact_kind": "anthropic.thinking_presence",
                        "provider": "anthropic",
                        "message_id": message_id,
                        "block_index": block_index,
                    },
                    payload=_block_descriptor(block),
                )
            )
            continue
        if block_type in _TOOL_CALL_TYPES:
            tool_id = block.get("id")
            name = block.get("name")
            tool_input = block.get("input")
            if not isinstance(tool_id, str) or not tool_id:
                raise EvidenceIngestError("tool use id must be a non-empty string")
            if not isinstance(name, str) or not name:
                raise EvidenceIngestError("tool use name must be a non-empty string")
            if not isinstance(tool_input, dict):
                raise EvidenceIngestError("tool use input must be an object")
            events.append(
                TraceEvent(
                    trace_id=trace_id,
                    sequence=len(events),
                    event_type=TraceEventType.TOOL_CALLED,
                    timestamp=timestamp,
                    context=context,
                    attributes={
                        "provider": "anthropic",
                        "tool_use_id": tool_id,
                        "tool_name": name,
                        "server_tool": block_type == "server_tool_use",
                        "message_id": message_id,
                        "origin": origin,
                    },
                    payload={
                        "input": _provider_safe(tool_input),
                        "caller": _provider_safe(block.get("caller")),
                    },
                )
            )
            continue
        if block_type in _TOOL_RESULT_TYPES:
            tool_id = block.get("tool_use_id")
            if not isinstance(tool_id, str) or not tool_id:
                raise EvidenceIngestError("tool result tool_use_id must be a non-empty string")
            linked = tool_index.get(tool_id)
            events.append(
                TraceEvent(
                    trace_id=trace_id,
                    sequence=len(events),
                    event_type=TraceEventType.TOOL_COMPLETED,
                    timestamp=timestamp,
                    context=context,
                    attributes={
                        "provider": "anthropic",
                        "tool_use_id": tool_id,
                        "tool_name": linked.get("name") if linked is not None else None,
                        "linked_call": linked is not None,
                        "server_tool": (
                            linked is not None and linked.get("type") == "server_tool_use"
                        ),
                        "is_error": bool(block.get("is_error", False)),
                        "result_type": block_type,
                        "message_id": message_id,
                        "origin": origin,
                    },
                    payload={
                        "content": _tool_result_content(block),
                        "caller": _provider_safe(block.get("caller")),
                    },
                )
            )
            continue
        if block_type != "text":
            events.append(
                TraceEvent(
                    trace_id=trace_id,
                    sequence=len(events),
                    event_type=TraceEventType.ARTIFACT_OBSERVED,
                    timestamp=timestamp,
                    context=context,
                    attributes={
                        "artifact_kind": "anthropic.content_block",
                        "provider": "anthropic",
                        "block_type": block_type,
                        "message_id": message_id,
                        "block_index": block_index,
                        "origin": origin,
                    },
                    payload=_block_descriptor(block),
                )
            )


def _append_message(
    events: list[TraceEvent],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    message: AnthropicInputMessage,
    tool_index: dict[str, dict[str, Any]],
    origin: str,
    request_id: str | None,
) -> None:
    blocks = _content_list(message.content, "message.content")
    rendered, descriptors = _render_blocks(blocks)
    events.append(
        TraceEvent(
            trace_id=trace_id,
            sequence=len(events),
            event_type=TraceEventType.MESSAGE_OBSERVED,
            timestamp=timestamp,
            context=context,
            attributes={
                "provider": "anthropic",
                "message_id": message.id,
                "role": message.role,
                "origin": origin,
                "request_id": request_id,
            },
            payload={
                "content": rendered,
                "blocks": descriptors,
                "metadata": message.metadata,
            },
        )
    )
    _append_block_events(
        events,
        trace_id=trace_id,
        context=context,
        timestamp=timestamp,
        message_id=message.id,
        blocks=blocks,
        tool_index=tool_index,
        origin=origin,
    )


def _response_artifact(record: AnthropicMessageRecord) -> dict[str, Any]:
    response = record.response
    return {
        "response_id": response["id"],
        "model": response["model"],
        "role": response["role"],
        "stop_reason": response.get("stop_reason"),
        "stop_sequence": response.get("stop_sequence"),
        "stop_details": _provider_safe(response.get("stop_details")),
        "usage": _provider_safe(response.get("usage")),
        "container": _provider_safe(response.get("container")),
        "request_id": record.request_id,
        "record_metadata": record.metadata,
        "content_block_types": [
            block.get("type") for block in cast(list[dict[str, Any]], response["content"])
        ],
    }


def import_anthropic_messages_document(
    document: AnthropicMessagesDocument,
    provenance: SourceProvenance,
    *,
    capsule_id: str | None = None,
    redaction_policy: RedactionPolicy | None = None,
) -> IngestionBundle:
    """Normalize a validated Anthropic archive into observable trace events."""
    selected_capsule = capsule_id or document.capsule_id
    context = TraceContext(
        run_id=document.id,
        capsule_id=selected_capsule,
        metadata={
            **document.metadata,
            "source_format": EvidenceFormat.ANTHROPIC_MESSAGES_JSON.value,
            "provider": "anthropic",
        },
    )
    first_timestamp = document.records[0].timestamp
    events = [
        TraceEvent(
            trace_id=document.id,
            sequence=0,
            event_type=TraceEventType.CONVERSATION_STARTED,
            timestamp=first_timestamp,
            context=context,
            payload={"conversation_id": document.id, "record_count": len(document.records)},
        )
    ]
    tool_index = _tool_index(document)
    seen_messages: dict[str, str] = {}
    for record_index, record in enumerate(document.records):
        system = _system_message(record)
        messages = ([system] if system is not None else []) + list(record.messages)
        for message in messages:
            signature = _observable_content_signature(message.role, message.content)
            if message.id in seen_messages:
                continue
            seen_messages[message.id] = signature
            _append_message(
                events,
                trace_id=document.id,
                context=context,
                timestamp=record.timestamp,
                message=message,
                tool_index=tool_index,
                origin="request",
                request_id=record.request_id,
            )
        response_message = AnthropicInputMessage(
            id=cast(str, record.response["id"]),
            role="assistant",
            content=cast(list[dict[str, Any]], record.response["content"]),
        )
        response_signature = _observable_content_signature(
            response_message.role,
            response_message.content,
        )
        if response_message.id not in seen_messages:
            seen_messages[response_message.id] = response_signature
            _append_message(
                events,
                trace_id=document.id,
                context=context,
                timestamp=record.timestamp,
                message=response_message,
                tool_index=tool_index,
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
                    "artifact_kind": "anthropic.message",
                    "provider": "anthropic",
                    "response_id": record.response["id"],
                    "record_index": record_index,
                },
                payload=_response_artifact(record),
            )
        )
    events.append(
        TraceEvent(
            trace_id=document.id,
            sequence=len(events),
            event_type=TraceEventType.ARTIFACT_OBSERVED,
            timestamp=document.records[-1].timestamp,
            context=context,
            attributes={
                "artifact_kind": "anthropic.conversation",
                "provider": "anthropic",
            },
            payload={
                "record_count": len(document.records),
                "observable_message_count": len(seen_messages),
                "tool_use_count": len(tool_index),
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


def ingest_anthropic_messages_file(
    path: Path,
    *,
    capsule_id: str | None = None,
    redact: bool = True,
    redaction_policy: RedactionPolicy | None = None,
) -> IngestionBundle:
    """Load and normalize an archived Anthropic Messages JSON export."""
    source = _load_json(
        path,
        EvidenceFormat.ANTHROPIC_MESSAGES_JSON,
        redact,
        redaction_policy,
    )
    try:
        document = AnthropicMessagesDocument.model_validate(source.data)
        return import_anthropic_messages_document(
            document,
            source.provenance,
            capsule_id=capsule_id,
            redaction_policy=redaction_policy,
        )
    except ValueError as exc:
        if isinstance(exc, EvidenceIngestError):
            raise
        raise EvidenceIngestError(str(exc)) from exc
