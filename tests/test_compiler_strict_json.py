from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.compiler import CapsuleCompileError, CompilerSpec, GoalSelector, GoalStrategy, compile_proposal
from e2h.ingest import EvidenceFormat, IngestionBundle, SourceProvenance
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

NOW = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)


def _spec(metadata: dict[str, Any] | None = None) -> CompilerSpec:
    return CompilerSpec(
        id="strict-compiler-json",
        goal=GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Run the check."),
        checks=[{"id": "check", "argv": ["python", "-V"]}],
        metadata={} if metadata is None else metadata,
    )


def _bundle() -> IngestionBundle:
    context = TraceContext(run_id="trace", capsule_id="source")
    return IngestionBundle(
        provenance=SourceProvenance(
            format=EvidenceFormat.TRANSCRIPT_JSON,
            source_name="source.json",
            sha256="a" * 64,
            size_bytes=1,
            redaction_enabled=True,
        ),
        traces=[
            Trace(
                trace_id="trace",
                events=[
                    TraceEvent(
                        trace_id="trace",
                        sequence=0,
                        event_type=TraceEventType.RUN_STARTED,
                        timestamp=NOW,
                        context=context,
                    ),
                    TraceEvent(
                        trace_id="trace",
                        sequence=1,
                        event_type=TraceEventType.RUN_COMPLETED,
                        timestamp=NOW + timedelta(seconds=1),
                        context=context,
                    ),
                ],
            )
        ],
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_compiler_spec_rejects_json_coercible_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="compiler metadata must be JSON-serializable"):
        _spec(metadata)


def test_compile_revalidation_rejects_mutated_json_coercion() -> None:
    spec = _spec()
    spec.metadata["nested"] = {1: "coerced key"}

    with pytest.raises(CapsuleCompileError, match="compiler metadata must be JSON-serializable"):
        compile_proposal(_bundle(), spec)


def test_compiler_spec_preserves_exact_nested_json() -> None:
    metadata = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}

    assert _spec(metadata).metadata == metadata
