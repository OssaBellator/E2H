"""Regression coverage for compiler content-addressing input revalidation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from e2h.compiler import (
    CapsuleCompileError,
    EnvironmentMutation,
    ProposalCore,
    bundle_digest,
    capsule_digest,
    mutation_plan_digest,
    proposal_digest,
)
from e2h.ingest import EvidenceFormat, IngestionBundle, SourceProvenance
from e2h.models import TaskCapsule
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

NOW = datetime(2026, 8, 10, 7, 30, tzinfo=UTC)
SHA = "a" * 64


def _capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "capsule",
            "goal": "Validate compiler digest boundaries.",
            "success": {
                "commands": [
                    {
                        "id": "check",
                        "argv": ["python", "-V"],
                    }
                ]
            },
        }
    )


def _trace() -> Trace:
    context = TraceContext(run_id="trace", capsule_id="capsule")
    return Trace(
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
                timestamp=NOW,
                context=context,
            ),
        ],
    )


def _bundle() -> IngestionBundle:
    return IngestionBundle(
        provenance=SourceProvenance(
            format=EvidenceFormat.TRANSCRIPT_JSON,
            source_name="source.json",
            sha256=SHA,
            size_bytes=1,
            redaction_enabled=True,
        ),
        traces=[_trace()],
    )


def _core() -> ProposalCore:
    return ProposalCore(
        capsule=_capsule(),
        provenance=SourceProvenance(
            format=EvidenceFormat.TRANSCRIPT_JSON,
            source_name="source.json",
            sha256=SHA,
            size_bytes=1,
            redaction_enabled=True,
        ),
        bundle_sha256=SHA,
    )


def test_capsule_digest_revalidates_mutated_capsule() -> None:
    capsule = _capsule()
    capsule.allowed_actions.network = "broken"  # type: ignore[assignment]

    with pytest.raises(CapsuleCompileError, match="invalid task capsule"):
        capsule_digest(capsule)


def test_bundle_digest_revalidates_mutated_bundle() -> None:
    bundle = _bundle()
    bundle.provenance.sha256 = "broken"

    with pytest.raises(CapsuleCompileError, match="invalid ingestion bundle"):
        bundle_digest(bundle)


def test_proposal_digest_revalidates_mutated_core() -> None:
    core = _core()
    core.capsule.allowed_actions.network = "broken"  # type: ignore[assignment]

    with pytest.raises(CapsuleCompileError, match="invalid proposal core"):
        proposal_digest(core)


def test_mutation_plan_digest_revalidates_mutated_entries() -> None:
    mutation = EnvironmentMutation(id="mutation", env={"MODE": "bad"})
    mutation.id = "invalid id"

    with pytest.raises(CapsuleCompileError, match="invalid mutation plan entry 0"):
        mutation_plan_digest([mutation])


def test_valid_compiler_digests_remain_stable() -> None:
    capsule = _capsule()
    bundle = _bundle()
    core = _core()
    mutations = [EnvironmentMutation(id="mutation", env={"MODE": "bad"})]

    assert capsule_digest(capsule) == capsule_digest(capsule.model_copy(deep=True))
    assert bundle_digest(bundle) == bundle_digest(bundle.model_copy(deep=True))
    assert proposal_digest(core) == proposal_digest(core.model_copy(deep=True))
    assert mutation_plan_digest(mutations) == mutation_plan_digest(
        [mutation.model_copy(deep=True) for mutation in mutations]
    )
