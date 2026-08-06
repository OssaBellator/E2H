"""Normalize archived OpenAI Responses API payloads into observable E2H traces."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.ingest import (
    EvidenceFormat,
    EvidenceIngestError,
    IngestionBundle,
    RedactionRecord,
    SourceProvenance,
    _apply_redaction,
    _load_json,
)
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

_MAX_PROVIDER_ITEMS = 10_000
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,255}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenAIResponseRecord(StrictModel):
    """One archived response plus the input items retrieved for that response."""

    response: dict[str, Any]
    input_items: list[dict[str, Any]] = Field(default_factory=list, max_length=_MAX_PROVIDER_ITEMS)
    request_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def response_must_have_core_fields(self) -> OpenAIResponseRecord:
        response_id = self.response.get("id")
        if not isinstance(response_id, str) or not response_id:
            raise ValueError("response.id must be a non-empty string")
        if self.response.get("object") != "response":
            raise ValueError("response.object must be 'response'")
        created_at = self.response.get("created_at")
        if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
            raise ValueError("response.created_at must be a Unix timestamp")
        if not math.isfinite(float(created_at)) or float(created_at) < 0:
            raise ValueError("response.created_at must be finite and non-negative")
        model = self.response.get("model")
        if not isinstance(model, str) or not model:
            raise ValueError("response.model must be a non-empty string")
        output = self.response.get("output")
        if not isinstance(output, list):
            raise ValueError("response.output must be an array")
        if len(output) > _MAX_PROVIDER_ITEMS:
            raise ValueError(f"response.output exceeds {_MAX_PROVIDER_ITEMS} items")
        for index, item in enumerate([*self.input_items, *output]):
            if not isinstance(item, dict):
                raise ValueError(f"provider item {index} must be an object")
            item_type = item.get("type")
            if not isinstance(item_type, str) or not item_type:
                raise ValueError(f"provider item {index}.type must be a non-empty string")
        try:
            json.dumps(self.response, sort_keys=True, allow_nan=False)
            json.dumps(self.input_items, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("response records must contain canonical JSON values") from exc
        return self


class OpenAIResponsesDocument(StrictModel):
    """Portable export envelope for one Responses API conversation or workflow."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    capsule_id: str = Field(default="unassigned", min_length=1, max_length=255)
    responses: list[OpenAIResponseRecord] = Field(min_length=1, max_length=_MAX_PROVIDER_ITEMS)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("document metadata must be JSON-serializable") from exc
        return value

    @model_validator(mode="after")
    def responses_must_be_ordered_and_unique(self) -> OpenAIResponsesDocument:
        response_ids: set[str] = set()
        previous_created_at: float | None = None
        item_count = 0
        for record in self.responses:
            response_id = cast(str, record.response["id"])
            if response_id in response_ids:
                raise ValueError("response ids must be unique")
            response_ids.add(response_id)
            created_at = float(cast(int | float, record.response["created_at"]))
            if previous_created_at is not None and created_at < previous_created_at:
                raise ValueError("response timestamps must be nondecreasing")
            previous_created_at = created_at
            item_count += len(record.input_items) + len(cast(list[Any], record.response["output"]))
            if item_count > _MAX_PROVIDER_ITEMS:
                raise ValueError(f"document exceeds {_MAX_PROVIDER_ITEMS} provider items")
        return self


def _timestamp(response: dict[str, Any]) -> datetime:
    try:
        return datetime.fromtimestamp(float(response["created_at"]), UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise EvidenceIngestError("response.created_at is outside the supported range") from exc


def _stable_item_key(item: dict[str, Any]) -> str | None:
    item_id = item.get("id")
    if isinstance(item_id, str) and item_id:
        return f"id:{item_id}"
    call_id = item.get("call_id")
    item_type = item.get("type")
    if isinstance(call_id, str) and call_id and isinstance(item_type, str):
        return f"call:{item_type}:{call_id}"
    return None


def _text_blocks(content: Any) -> tuple[str, list[dict[str, Any]]]:
    if not isinstance(content, list):
        raise EvidenceIngestError("message content must be an array")
    rendered: list[str] = []
    blocks: list[dict[str, Any]] = []
    for index, raw_block in enumerate(content):
        if not isinstance(raw_block, dict):
            raise EvidenceIngestError(f"message content block {index} must be an object")
        block = cast(dict[str, Any], raw_block)
        block_type = block.get("type")
        if not isinstance(block_type, str) or not block_type:
            raise EvidenceIngestError(f"message content block {index}.type must be a string")
        blocks.append(block)
        if block_type in {"input_text", "output_text"}:
            text = block.get("text")
            if not isinstance(text, str):
                raise EvidenceIngestError(f"message content block {index}.text must be a string")
            rendered.append(text)
        elif block_type == "refusal":
            refusal = block.get("refusal")
            if not isinstance(refusal, str):
                raise EvidenceIngestError(f"message content block {index}.refusal must be a string")
            rendered.append(refusal)
        elif block_type == "input_image":
            rendered.append("[input_image]")
        elif block_type == "input_file":
            rendered.append("[input_file]")
        else:
            rendered.append(f"[{block_type}]")
    return "\n".join(part for part in rendered if part), blocks


def _json_arguments(value: str) -> Any | None:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _append_message(
    events: list[TraceEvent],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    response_id: str,
    item: dict[str, Any],
    origin: str,
) -> None:
    role = item.get("role")
    if not isinstance(role, str) or not role:
        raise EvidenceIngestError("message.role must be a non-empty string")
    message_id = item.get("id")
    if not isinstance(message_id, str) or not message_id:
        raise EvidenceIngestError("message.id must be a non-empty string")
    content, blocks = _text_blocks(item.get("content"))
    events.append(
        TraceEvent(
            trace_id=trace_id,
            sequence=len(events),
            event_type=TraceEventType.MESSAGE_OBSERVED,
            timestamp=timestamp,
            context=context,
            attributes={
                "message_id": message_id,
                "role": role,
                "provider": "openai",
                "response_id": response_id,
                "item_origin": origin,
            },
            payload={
                "content": content,
                "content_blocks": blocks,
                "status": item.get("status"),
                "phase": item.get("phase"),
            },
        )
    )


def _append_function_call(
    events: list[TraceEvent],
    calls: dict[str, dict[str, Any]],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    response_id: str,
    item: dict[str, Any],
    origin: str,
) -> None:
    call_id = item.get("call_id")
    name = item.get("name")
    arguments = item.get("arguments")
    if not isinstance(call_id, str) or not call_id:
        raise EvidenceIngestError("function_call.call_id must be a non-empty string")
    if not isinstance(name, str) or not name:
        raise EvidenceIngestError("function_call.name must be a non-empty string")
    if not isinstance(arguments, str):
        raise EvidenceIngestError("function_call.arguments must be a string")
    calls[call_id] = {
        "name": name,
        "namespace": item.get("namespace"),
        "provider_item_id": item.get("id"),
    }
    events.append(
        TraceEvent(
            trace_id=trace_id,
            sequence=len(events),
            event_type=TraceEventType.TOOL_CALLED,
            timestamp=timestamp,
            context=context,
            attributes={
                "tool_call_id": call_id,
                "tool_name": name,
                "provider": "openai",
                "response_id": response_id,
                "item_origin": origin,
            },
            payload={
                "arguments": arguments,
                "arguments_json": _json_arguments(arguments),
                "namespace": item.get("namespace"),
                "status": item.get("status"),
                "provider_item_id": item.get("id"),
                "caller": item.get("caller"),
            },
        )
    )


def _append_function_output(
    events: list[TraceEvent],
    calls: dict[str, dict[str, Any]],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    response_id: str,
    item: dict[str, Any],
    origin: str,
) -> None:
    call_id = item.get("call_id")
    if not isinstance(call_id, str) or not call_id:
        raise EvidenceIngestError("function_call_output.call_id must be a non-empty string")
    output = item.get("output")
    if isinstance(output, str):
        output_text = output
        output_blocks: list[dict[str, Any]] | None = None
    elif isinstance(output, list):
        output_text, output_blocks = _text_blocks(output)
    else:
        raise EvidenceIngestError("function_call_output.output must be a string or array")
    linked = calls.get(call_id)
    name = item.get("name")
    if not isinstance(name, str) or not name:
        name = linked.get("name") if linked is not None else None
    events.append(
        TraceEvent(
            trace_id=trace_id,
            sequence=len(events),
            event_type=TraceEventType.TOOL_COMPLETED,
            timestamp=timestamp,
            context=context,
            attributes={
                "tool_call_id": call_id,
                "tool_name": name,
                "provider": "openai",
                "response_id": response_id,
                "item_origin": origin,
                "linked_call": linked is not None,
            },
            payload={
                "output": output_text,
                "output_blocks": output_blocks,
                "status": item.get("status"),
                "provider_item_id": item.get("id"),
                "namespace": item.get("namespace"),
                "created_by": item.get("created_by"),
                "caller": item.get("caller"),
            },
        )
    )


def _append_reasoning_summary(
    events: list[TraceEvent],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    response_id: str,
    item: dict[str, Any],
    origin: str,
) -> None:
    raw_summary = item.get("summary", [])
    if not isinstance(raw_summary, list):
        raise EvidenceIngestError("reasoning.summary must be an array")
    summaries: list[str] = []
    for index, raw in enumerate(raw_summary):
        if not isinstance(raw, dict) or raw.get("type") != "summary_text":
            raise EvidenceIngestError(f"reasoning.summary/{index} must be summary_text")
        text = raw.get("text")
        if not isinstance(text, str):
            raise EvidenceIngestError(f"reasoning.summary/{index}.text must be a string")
        summaries.append(text)
    events.append(
        TraceEvent(
            trace_id=trace_id,
            sequence=len(events),
            event_type=TraceEventType.ARTIFACT_OBSERVED,
            timestamp=timestamp,
            context=context,
            attributes={
                "artifact_kind": "openai.reasoning_summary",
                "provider": "openai",
                "response_id": response_id,
                "item_origin": origin,
            },
            payload={
                "provider_item_id": item.get("id"),
                "summary": summaries,
                "status": item.get("status"),
                "reasoning_content_present": bool(item.get("content")),
                "encrypted_content_present": bool(item.get("encrypted_content")),
            },
        )
    )


def _append_provider_artifact(
    events: list[TraceEvent],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    response_id: str,
    item: dict[str, Any],
    origin: str,
) -> None:
    events.append(
        TraceEvent(
            trace_id=trace_id,
            sequence=len(events),
            event_type=TraceEventType.ARTIFACT_OBSERVED,
            timestamp=timestamp,
            context=context,
            attributes={
                "artifact_kind": "openai.response_item",
                "provider": "openai",
                "response_id": response_id,
                "item_origin": origin,
                "item_type": item.get("type"),
            },
            payload={"item": item},
        )
    )


def _append_item(
    events: list[TraceEvent],
    calls: dict[str, dict[str, Any]],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    response_id: str,
    item: dict[str, Any],
    origin: str,
) -> None:
    item_type = item.get("type")
    if item_type == "message":
        _append_message(
            events,
            trace_id=trace_id,
            context=context,
            timestamp=timestamp,
            response_id=response_id,
            item=item,
            origin=origin,
        )
    elif item_type == "function_call":
        _append_function_call(
            events,
            calls,
            trace_id=trace_id,
            context=context,
            timestamp=timestamp,
            response_id=response_id,
            item=item,
            origin=origin,
        )
    elif item_type == "function_call_output":
        _append_function_output(
            events,
            calls,
            trace_id=trace_id,
            context=context,
            timestamp=timestamp,
            response_id=response_id,
            item=item,
            origin=origin,
        )
    elif item_type == "reasoning":
        _append_reasoning_summary(
            events,
            trace_id=trace_id,
            context=context,
            timestamp=timestamp,
            response_id=response_id,
            item=item,
            origin=origin,
        )
    else:
        _append_provider_artifact(
            events,
            trace_id=trace_id,
            context=context,
            timestamp=timestamp,
            response_id=response_id,
            item=item,
            origin=origin,
        )


def _append_instructions(
    events: list[TraceEvent],
    seen_items: set[str],
    calls: dict[str, dict[str, Any]],
    *,
    trace_id: str,
    context: TraceContext,
    timestamp: datetime,
    response_id: str,
    instructions: Any,
) -> None:
    if isinstance(instructions, str):
        if not instructions:
            return
        synthetic_id = f"{response_id}.instructions"
        if synthetic_id in seen_items:
            return
        seen_items.add(synthetic_id)
        _append_message(
            events,
            trace_id=trace_id,
            context=context,
            timestamp=timestamp,
            response_id=response_id,
            item={
                "id": synthetic_id,
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": instructions}],
                "status": "completed",
            },
            origin="response.instructions",
        )
    elif isinstance(instructions, list):
        for item in instructions:
            if not isinstance(item, dict):
                raise EvidenceIngestError("response.instructions items must be objects")
            key = _stable_item_key(item)
            if key is not None and key in seen_items:
                continue
            if key is not None:
                seen_items.add(key)
            _append_item(
                events,
                calls,
                trace_id=trace_id,
                context=context,
                timestamp=timestamp,
                response_id=response_id,
                item=cast(dict[str, Any], item),
                origin="response.instructions",
            )
    elif instructions is not None:
        raise EvidenceIngestError("response.instructions must be a string, array, or null")


def _response_metadata(response: dict[str, Any], record: OpenAIResponseRecord) -> dict[str, Any]:
    conversation = response.get("conversation")
    conversation_id = conversation.get("id") if isinstance(conversation, dict) else conversation
    return {
        "response_id": response["id"],
        "model": response["model"],
        "status": response.get("status"),
        "created_at": response["created_at"],
        "previous_response_id": response.get("previous_response_id"),
        "conversation_id": conversation_id,
        "usage": response.get("usage"),
        "error": response.get("error"),
        "incomplete_details": response.get("incomplete_details"),
        "metadata": response.get("metadata"),
        "request_id": record.request_id,
        "background": response.get("background"),
        "parallel_tool_calls": response.get("parallel_tool_calls"),
        "service_tier": response.get("service_tier"),
        "input_item_ids": [item.get("id") for item in record.input_items],
        "output_item_ids": [
            item.get("id") for item in cast(list[dict[str, Any]], response["output"])
        ],
    }


def _index_function_calls(
    document: OpenAIResponsesDocument,
) -> dict[str, dict[str, Any]]:
    """Index observable function calls before emitting any output events."""
    calls: dict[str, dict[str, Any]] = {}
    for record in document.responses:
        output = cast(list[dict[str, Any]], record.response["output"])
        for item in [*record.input_items, *output]:
            if item.get("type") != "function_call":
                continue
            call_id = item.get("call_id")
            name = item.get("name")
            if not isinstance(call_id, str) or not call_id:
                continue
            if not isinstance(name, str) or not name:
                continue
            metadata = {
                "name": name,
                "namespace": item.get("namespace"),
                "provider_item_id": item.get("id"),
            }
            existing = calls.get(call_id)
            if existing is not None and existing != metadata:
                raise EvidenceIngestError(
                    f"function call {call_id!r} has conflicting provider metadata"
                )
            calls[call_id] = metadata
    return calls


def import_openai_responses_document(
    document: OpenAIResponsesDocument,
    provenance: SourceProvenance,
    *,
    capsule_id: str | None = None,
) -> IngestionBundle:
    """Normalize an archived Responses export into one observable trace."""
    selected_capsule = capsule_id or document.capsule_id
    first_timestamp = _timestamp(document.responses[0].response)
    context = TraceContext(
        run_id=document.id,
        capsule_id=selected_capsule,
        metadata={
            **document.metadata,
            "provider": "openai",
            "source_format": EvidenceFormat.OPENAI_RESPONSES_JSON.value,
            "response_count": len(document.responses),
        },
    )
    events = [
        TraceEvent(
            trace_id=document.id,
            sequence=0,
            event_type=TraceEventType.CONVERSATION_STARTED,
            timestamp=first_timestamp,
            context=context,
            payload={"conversation_id": document.id, "response_count": len(document.responses)},
        )
    ]
    seen_items: set[str] = set()
    calls = _index_function_calls(document)
    for record in document.responses:
        response = record.response
        response_id = cast(str, response["id"])
        timestamp = _timestamp(response)
        _append_instructions(
            events,
            seen_items,
            calls,
            trace_id=document.id,
            context=context,
            timestamp=timestamp,
            response_id=response_id,
            instructions=response.get("instructions"),
        )
        for origin, items in (
            ("input_items", record.input_items),
            ("response.output", cast(list[dict[str, Any]], response["output"])),
        ):
            for item in items:
                key = _stable_item_key(item)
                if key is not None and key in seen_items:
                    continue
                if key is not None:
                    seen_items.add(key)
                _append_item(
                    events,
                    calls,
                    trace_id=document.id,
                    context=context,
                    timestamp=timestamp,
                    response_id=response_id,
                    item=item,
                    origin=origin,
                )
        events.append(
            TraceEvent(
                trace_id=document.id,
                sequence=len(events),
                event_type=TraceEventType.ARTIFACT_OBSERVED,
                timestamp=timestamp,
                context=context,
                attributes={
                    "artifact_kind": "openai.response",
                    "provider": "openai",
                    "response_id": response_id,
                },
                payload=_response_metadata(response, record),
            )
        )
    trace = Trace(trace_id=document.id, events=events)
    redactions: list[RedactionRecord] = []
    if provenance.redaction_enabled:
        trace = _apply_redaction(trace, redactions, trace_index=0)
    return IngestionBundle(
        provenance=provenance,
        traces=[trace],
        corrections=[],
        redactions=redactions,
    )


def ingest_openai_responses_file(
    path: Path,
    *,
    capsule_id: str | None = None,
    redact: bool = True,
) -> IngestionBundle:
    """Load and normalize an archived OpenAI Responses export."""
    source = _load_json(path, EvidenceFormat.OPENAI_RESPONSES_JSON, redact)
    try:
        document = OpenAIResponsesDocument.model_validate(source.data)
        return import_openai_responses_document(
            document,
            source.provenance,
            capsule_id=capsule_id,
        )
    except ValueError as exc:
        if isinstance(exc, EvidenceIngestError):
            raise
        raise EvidenceIngestError(str(exc)) from exc
