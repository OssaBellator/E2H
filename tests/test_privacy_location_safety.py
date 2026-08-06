from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from e2h.privacy import (
    RedactionPolicy,
    apply_redaction_policy,
)
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

SENSITIVE_KEY = "allowed@example.com"


def _trace() -> Trace:
    context = TraceContext(run_id="location-safety", capsule_id="location-safety")
    timestamp = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)
    return Trace(
        trace_id="location-safety",
        events=[
            TraceEvent(
                trace_id="location-safety",
                sequence=0,
                event_type=TraceEventType.MESSAGE_OBSERVED,
                timestamp=timestamp,
                context=context,
                payload={SENSITIVE_KEY: "api_key=secretvalue123"},
            ),
            TraceEvent(
                trace_id="location-safety",
                sequence=1,
                event_type=TraceEventType.ARTIFACT_OBSERVED,
                timestamp=timestamp,
                context=context,
            ),
        ],
    )


@pytest.mark.parametrize("redaction_enabled", [True, False])
def test_mapping_keys_never_appear_in_review_locations(redaction_enabled: bool) -> None:
    outcome = apply_redaction_policy(
        [_trace()],
        policy=RedactionPolicy(allow_values=[SENSITIVE_KEY]),
        redaction_enabled=redaction_enabled,
    )

    records = json.dumps(
        [record.model_dump(mode="json") for record in outcome.records],
        sort_keys=True,
    )
    review = outcome.review.model_dump_json()

    assert SENSITIVE_KEY not in records
    assert SENSITIVE_KEY not in review
    locations = [record.location for record in outcome.records]
    locations.extend(finding.location for finding in outcome.review.residual_findings)
    assert any("/<value:0>" in location for location in locations)
