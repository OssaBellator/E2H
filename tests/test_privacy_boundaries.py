from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

from e2h.privacy import (
    CustomRedactionRule,
    RedactionPolicy,
    RedactionPolicyError,
    apply_redaction_policy,
)
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _PolicySubclass(RedactionPolicy):
    pass


class _TraceSubclass(Trace):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def policy() -> RedactionPolicy:
    return RedactionPolicy(
        id="privacy-boundary",
        custom_rules=[CustomRedactionRule(id="ticket", pattern=r"TICKET-[0-9]+")],
        allow_values=["keep@example.com"],
    )


def trace() -> Trace:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    context = TraceContext(run_id="privacy-boundary", capsule_id="capsule")
    return Trace(
        trace_id="privacy-boundary",
        events=[
            TraceEvent(
                trace_id="privacy-boundary",
                sequence=0,
                event_type=TraceEventType.RUN_STARTED,
                timestamp=started,
                context=context,
                payload={"message": "contact user@example.com with TICKET-42"},
            ),
            TraceEvent(
                trace_id="privacy-boundary",
                sequence=1,
                event_type=TraceEventType.RUN_COMPLETED,
                timestamp=started + timedelta(seconds=1),
                context=context,
            ),
        ],
    )


def test_policy_boundary_revalidates_invalid_custom_regex() -> None:
    candidate = policy()
    candidate.custom_rules[0].pattern = "["

    with pytest.raises(RedactionPolicyError, match="invalid redaction policy"):
        apply_redaction_policy([trace()], policy=candidate)


def test_policy_boundary_revalidates_allowlist_constraints() -> None:
    duplicate = policy()
    duplicate.allow_values = ["same", "same"]
    with pytest.raises(RedactionPolicyError, match="allow_values entries must be unique"):
        apply_redaction_policy([trace()], policy=duplicate)

    over_limit = policy()
    over_limit.allow_values = [f"value-{index}" for index in range(1_001)]
    with pytest.raises(RedactionPolicyError, match="invalid redaction policy"):
        apply_redaction_policy([trace()], policy=over_limit)


def test_policy_boundary_rejects_subclass_and_lookalike() -> None:
    candidate = policy()
    subclassed = _PolicySubclass.model_validate(candidate.model_dump(mode="json"))
    lookalike = _Lookalike.model_validate(candidate.model_dump(mode="json"))

    with pytest.raises(
        RedactionPolicyError,
        match="expected RedactionPolicy, got _PolicySubclass",
    ):
        apply_redaction_policy([trace()], policy=subclassed)

    with pytest.raises(
        RedactionPolicyError,
        match="expected RedactionPolicy, got _Lookalike",
    ):
        apply_redaction_policy([trace()], policy=cast(Any, lookalike))


def test_trace_boundary_rejects_subclass_and_lookalike() -> None:
    candidate = trace()
    subclassed = _TraceSubclass.model_validate(candidate.model_dump(mode="json"))
    lookalike = _Lookalike.model_validate(candidate.model_dump(mode="json"))

    with pytest.raises(
        RedactionPolicyError,
        match="expected Trace, got _TraceSubclass",
    ):
        apply_redaction_policy([subclassed], policy=policy())

    with pytest.raises(
        RedactionPolicyError,
        match="expected Trace, got _Lookalike",
    ):
        apply_redaction_policy([cast(Any, lookalike)], policy=policy())


@pytest.mark.parametrize("redaction_enabled", [True, False])
def test_trace_boundary_revalidates_sequence_before_any_scan(redaction_enabled: bool) -> None:
    candidate = trace()
    candidate.events[1].sequence = 3

    with pytest.raises(RedactionPolicyError, match="invalid trace at index 0"):
        apply_redaction_policy(
            [candidate],
            policy=policy(),
            redaction_enabled=redaction_enabled,
        )


@pytest.mark.parametrize("redaction_enabled", [True, False])
def test_trace_boundary_revalidates_timestamp_order(redaction_enabled: bool) -> None:
    candidate = trace()
    candidate.events[1].timestamp = candidate.events[0].timestamp - timedelta(seconds=1)

    with pytest.raises(RedactionPolicyError, match="invalid trace at index 0"):
        apply_redaction_policy(
            [candidate],
            policy=policy(),
            redaction_enabled=redaction_enabled,
        )


@pytest.mark.parametrize("redaction_enabled", [True, False])
def test_trace_boundary_revalidates_json_state(redaction_enabled: bool) -> None:
    candidate = trace()
    candidate.events[0].payload = {"invalid": object()}

    with pytest.raises(RedactionPolicyError, match="invalid trace at index 0"):
        apply_redaction_policy(
            [candidate],
            policy=policy(),
            redaction_enabled=redaction_enabled,
        )


def test_boundary_normalizes_warning_prone_raw_nested_assignments() -> None:
    candidate_policy = policy()
    candidate_policy.custom_rules = [candidate_policy.custom_rules[0].model_dump(mode="json")]
    candidate_trace = trace()
    candidate_trace.events = [event.model_dump(mode="json") for event in candidate_trace.events]

    outcome = apply_redaction_policy([candidate_trace], policy=candidate_policy)

    assert outcome.review.policy_id == "privacy-boundary"
    assert outcome.review.total_redactions == 2
    assert outcome.review.residual_findings == []
