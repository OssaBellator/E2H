from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType, write_traces_jsonl

NOW = datetime(2026, 8, 9, 14, 0, tzinfo=UTC)


class CoercibleInt(int):
    pass


def _event(sequence: int, **overrides: Any) -> TraceEvent:
    payload: dict[str, Any] = {
        "trace_id": "trace",
        "sequence": sequence,
        "event_type": TraceEventType.RUN_STARTED,
        "timestamp": NOW + timedelta(seconds=sequence),
        "context": TraceContext(run_id="trace", capsule_id="capsule"),
    }
    payload.update(overrides)
    return TraceEvent.model_validate(payload)


def test_trace_event_rejects_tuple_payload_instead_of_normalizing_to_list() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        _event(0, payload={"value": (1, 2)})


def test_trace_event_rejects_coercible_scalar_subclass() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        _event(0, attributes={"value": CoercibleInt(3)})


def test_trace_context_rejects_tuple_metadata() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        TraceContext(
            run_id="trace",
            capsule_id="capsule",
            metadata={"value": (1, 2)},
        )


def test_writer_rejects_tuple_added_after_validation(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    trace.events[0].payload["value"] = (1, 2)
    output = tmp_path / "traces.jsonl"

    with pytest.raises(ValueError, match="non-JSON tuple"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_writer_rejects_coercible_scalar_added_after_validation(tmp_path: Path) -> None:
    trace = Trace(trace_id="trace", events=[_event(0), _event(1)])
    trace.events[0].attributes["value"] = CoercibleInt(3)
    output = tmp_path / "traces.jsonl"

    with pytest.raises(ValueError, match="unsupported value type CoercibleInt"):
        write_traces_jsonl(output, [trace])

    assert not output.exists()


def test_trace_free_form_fields_preserve_exact_nested_json() -> None:
    nested = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}
    context = TraceContext(run_id="trace", capsule_id="capsule", metadata=nested)
    event = _event(0, context=context, attributes={"nested": nested}, payload={"nested": nested})

    assert event.context.metadata == nested
    assert event.attributes["nested"] == nested
    assert event.payload["nested"] == nested
