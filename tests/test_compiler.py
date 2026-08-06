from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.compiler import (
    CapsuleCompileError,
    CapsuleProposal,
    CompilerSpec,
    EnvironmentMutation,
    GoalSelector,
    GoalStrategy,
    ReviewDecision,
    VerificationReport,
    bundle_digest,
    compile_proposal,
    load_compiler_spec,
    load_ingestion_bundle,
    load_proposal,
    load_verification_report,
    materialize_capsule,
    proposal_digest,
    review_proposal,
    verify_proposal,
)
from e2h.ingest import (
    CorrectionRecord,
    EvidenceFormat,
    IngestionBundle,
    SourceProvenance,
)
from e2h.models import CommandCheck
from e2h.runner import RunStatus
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

NOW = datetime(2026, 8, 6, 4, 0, tzinfo=UTC)


def message_event(
    sequence: int,
    message_id: str,
    role: str,
    content: str,
    *,
    seconds: int,
) -> TraceEvent:
    return TraceEvent(
        trace_id="conversation-1",
        sequence=sequence,
        event_type=TraceEventType.MESSAGE_OBSERVED,
        timestamp=NOW.replace(second=seconds),
        context=TraceContext(run_id="conversation-1", capsule_id="demo"),
        attributes={"message_id": message_id, "role": role},
        payload={"content": content},
    )


def transcript_bundle(*, redacted: bool = True, corrections: bool = True) -> IngestionBundle:
    events = [
        TraceEvent(
            trace_id="conversation-1",
            sequence=0,
            event_type=TraceEventType.CONVERSATION_STARTED,
            timestamp=NOW,
            context=TraceContext(run_id="conversation-1", capsule_id="demo"),
            payload={"conversation_id": "conversation-1"},
        ),
        message_event(1, "m1", "user", "Check the billing result.", seconds=1),
        message_event(2, "m2", "assistant", "The result passed.", seconds=2),
        message_event(3, "m3", "user", "The result is wrong; rerun the contract check.", seconds=3),
        TraceEvent(
            trace_id="conversation-1",
            sequence=4,
            event_type=TraceEventType.FEEDBACK_OBSERVED,
            timestamp=NOW.replace(second=3),
            context=TraceContext(run_id="conversation-1", capsule_id="demo"),
            payload={"message_id": "m3", "correction_of": "m2"},
        ),
        message_event(5, "m4", "user", "Also verify the generated artifact.", seconds=4),
    ]
    return IngestionBundle(
        provenance=SourceProvenance(
            format=EvidenceFormat.TRANSCRIPT_JSON,
            source_name="transcript.json",
            sha256="a" * 64,
            size_bytes=123,
            redaction_enabled=redacted,
        ),
        traces=[Trace(trace_id="conversation-1", events=events)],
        corrections=(
            [
                CorrectionRecord(
                    trace_id="conversation-1",
                    message_id="m3",
                    correction_of="m2",
                    event_sequence=4,
                )
            ]
            if corrections
            else []
        ),
    )


def command_check() -> CommandCheck:
    code = "import os; raise SystemExit(0 if os.getenv('MODE', 'good') == 'good' else 7)"
    return CommandCheck(id="contract", argv=[sys.executable, "-c", code])


def compiler_spec(
    *,
    goal: GoalSelector | None = None,
    mutations: list[EnvironmentMutation] | None = None,
    allow_unredacted: bool = False,
) -> CompilerSpec:
    return CompilerSpec(
        id="compiled-capsule",
        goal=goal or GoalSelector(),
        checks=[command_check()],
        mutations=(
            mutations
            if mutations is not None
            else [EnvironmentMutation(id="break-mode", env={"MODE": "bad"})]
        ),
        allow_unredacted=allow_unredacted,
        metadata={"suite": "compiler"},
    )


def test_compile_latest_correction_with_evidence_references() -> None:
    bundle = transcript_bundle()
    proposal = compile_proposal(bundle, compiler_spec())
    assert proposal.core.capsule.goal == "The result is wrong; rerun the contract check."
    assert [reference.message_id for reference in proposal.core.evidence] == ["m2", "m3", None]
    assert proposal.proposal_id == proposal_digest(proposal.core)
    assert proposal.core.bundle_sha256 == bundle_digest(bundle)
    compiler_metadata = proposal.core.capsule.metadata["e2h_compiler"]
    assert compiler_metadata["source_sha256"] == "a" * 64
    assert compiler_metadata["source_format"] == "transcript-json"


def test_compile_latest_user_message() -> None:
    selector = GoalSelector(strategy=GoalStrategy.LATEST_USER_MESSAGE)
    proposal = compile_proposal(transcript_bundle(), compiler_spec(goal=selector))
    assert proposal.core.capsule.goal == "Also verify the generated artifact."
    assert len(proposal.core.evidence) == 1
    assert proposal.core.evidence[0].message_id == "m4"


def test_compile_explicit_goal_warns_and_has_no_evidence() -> None:
    selector = GoalSelector(strategy=GoalStrategy.EXPLICIT, text="Preserve the API contract.")
    proposal = compile_proposal(transcript_bundle(), compiler_spec(goal=selector))
    assert proposal.core.capsule.goal == "Preserve the API contract."
    assert proposal.core.evidence == []
    assert any("explicit" in warning for warning in proposal.core.warnings)


def test_unredacted_bundle_requires_explicit_opt_in() -> None:
    with pytest.raises(CapsuleCompileError, match="unredacted"):
        compile_proposal(transcript_bundle(redacted=False), compiler_spec())
    proposal = compile_proposal(
        transcript_bundle(redacted=False),
        compiler_spec(allow_unredacted=True),
    )
    assert any("unredacted" in warning for warning in proposal.core.warnings)


def test_latest_correction_requires_correction() -> None:
    with pytest.raises(CapsuleCompileError, match="no explicit correction"):
        compile_proposal(transcript_bundle(corrections=False), compiler_spec())


def test_selected_trace_must_exist() -> None:
    selector = GoalSelector(strategy=GoalStrategy.LATEST_USER_MESSAGE, trace_id="missing")
    with pytest.raises(CapsuleCompileError, match="selected trace"):
        compile_proposal(transcript_bundle(), compiler_spec(goal=selector))


def test_missing_correction_message_is_rejected() -> None:
    bundle = transcript_bundle()
    bundle.corrections[0].message_id = "missing"
    with pytest.raises(CapsuleCompileError, match="exactly one"):
        compile_proposal(bundle, compiler_spec())


def test_selected_message_requires_content() -> None:
    bundle = transcript_bundle()
    bundle.traces[0].events[3].payload["content"] = ""
    with pytest.raises(CapsuleCompileError, match="non-empty content"):
        compile_proposal(bundle, compiler_spec())


def test_proposal_tampering_is_detected() -> None:
    proposal = compile_proposal(transcript_bundle(), compiler_spec())
    payload = proposal.model_dump()
    payload["core"]["capsule"]["goal"] = "tampered"
    with pytest.raises(ValidationError, match="proposal_id"):
        CapsuleProposal.model_validate(payload)


def test_environment_mutation_validation() -> None:
    for env in (
        {"": "value"},
        {"A=B": "value"},
        {"KEY": "bad\x00value"},
        {"e2h_mutation_id": "spoof"},
        {"E2H_PROPOSAL_ID": "spoof"},
    ):
        with pytest.raises(ValidationError):
            EnvironmentMutation(id="invalid", env=env)


def test_mutation_targets_must_exist() -> None:
    with pytest.raises(ValidationError, match="unknown checks"):
        compiler_spec(
            mutations=[EnvironmentMutation(id="bad", env={"MODE": "bad"}, check_ids=["missing"])]
        )


def test_mutation_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="mutation ids"):
        compiler_spec(
            mutations=[
                EnvironmentMutation(id="same", env={"MODE": "bad"}),
                EnvironmentMutation(id="same", env={"MODE": "worse"}),
            ]
        )


def test_goal_selector_fields_must_match() -> None:
    with pytest.raises(ValidationError, match="requires"):
        GoalSelector(strategy=GoalStrategy.EXPLICIT)
    with pytest.raises(ValidationError, match="only valid"):
        GoalSelector(strategy=GoalStrategy.LATEST_USER_MESSAGE, text="not allowed")


def test_verification_detects_mutation(tmp_path: Path) -> None:
    proposal = compile_proposal(transcript_bundle(), compiler_spec())
    report = verify_proposal(proposal, tmp_path)
    assert report.baseline.status is RunStatus.PASSED
    assert report.mutations[0].result.status is RunStatus.FAILED
    assert report.mutations[0].detected is True
    assert report.strong is True


def test_verification_fails_when_mutation_is_not_detected(tmp_path: Path) -> None:
    spec = compiler_spec(mutations=[EnvironmentMutation(id="noop", env={"UNUSED": "1"})])
    report = verify_proposal(compile_proposal(transcript_bundle(), spec), tmp_path)
    assert report.baseline_passed is True
    assert report.all_mutations_detected is False
    assert report.strong is False


def test_verification_fails_when_baseline_fails(tmp_path: Path) -> None:
    check = command_check()
    check.env = {"MODE": "bad"}
    spec = CompilerSpec(
        id="bad-baseline",
        checks=[check],
        mutations=[EnvironmentMutation(id="also-bad", env={"MODE": "bad"})],
    )
    report = verify_proposal(compile_proposal(transcript_bundle(), spec), tmp_path)
    assert report.baseline.status is RunStatus.FAILED
    assert report.strong is False


def test_no_mutations_cannot_be_strong(tmp_path: Path) -> None:
    proposal = compile_proposal(transcript_bundle(), compiler_spec(mutations=[]))
    report = verify_proposal(proposal, tmp_path)
    assert report.mutations == []
    assert report.all_mutations_detected is False
    assert report.strong is False
    assert any("no mutation" in warning for warning in proposal.core.warnings)


def test_review_and_materialize_gates(tmp_path: Path) -> None:
    proposal = compile_proposal(transcript_bundle(), compiler_spec())
    report = verify_proposal(proposal, tmp_path)
    with pytest.raises(CapsuleCompileError, match="not approved"):
        materialize_capsule(proposal, report)

    approved = review_proposal(
        proposal,
        reviewer="reviewer@example",
        decision=ReviewDecision.APPROVE,
        timestamp=NOW,
    )
    capsule = materialize_capsule(approved, report)
    assert capsule.id == "compiled-capsule"
    assert approved.approved is True
    assert approved.proposal_id == proposal.proposal_id

    rejected = review_proposal(
        approved,
        reviewer="second-reviewer",
        decision=ReviewDecision.REJECT,
        timestamp=NOW,
    )
    assert rejected.approved is False
    with pytest.raises(CapsuleCompileError, match="not approved"):
        materialize_capsule(rejected, report)


def test_materialize_requires_matching_strong_report(tmp_path: Path) -> None:
    proposal = compile_proposal(transcript_bundle(), compiler_spec())
    approved = review_proposal(
        proposal,
        reviewer="reviewer",
        decision=ReviewDecision.APPROVE,
        timestamp=NOW,
    )
    report = verify_proposal(proposal, tmp_path)
    payload = report.model_dump()
    payload["proposal_id"] = "b" * 64
    other = VerificationReport.model_validate(payload)
    with pytest.raises(CapsuleCompileError, match="does not match"):
        materialize_capsule(approved, other)

    weak_spec = compiler_spec(mutations=[])
    weak_proposal = compile_proposal(transcript_bundle(), weak_spec)
    weak_approved = review_proposal(
        weak_proposal,
        reviewer="reviewer",
        decision=ReviewDecision.APPROVE,
        timestamp=NOW,
    )
    weak_report = verify_proposal(weak_proposal, tmp_path)
    with pytest.raises(CapsuleCompileError, match="strong"):
        materialize_capsule(weak_approved, weak_report)


def test_review_timestamp_must_be_aware() -> None:
    proposal = compile_proposal(transcript_bundle(), compiler_spec())
    with pytest.raises(ValidationError, match="timezone-aware"):
        review_proposal(
            proposal,
            reviewer="reviewer",
            decision=ReviewDecision.APPROVE,
            timestamp=datetime(2026, 8, 6),
        )


def test_bundle_and_proposal_digests_are_stable() -> None:
    bundle = transcript_bundle()
    first = compile_proposal(bundle, compiler_spec())
    second = compile_proposal(IngestionBundle.model_validate(bundle.model_dump()), compiler_spec())
    assert first.proposal_id == second.proposal_id
    assert bundle_digest(bundle) == bundle_digest(bundle)


def test_loaders_support_yaml_spec_and_json_artifacts(tmp_path: Path) -> None:
    spec_path = tmp_path / "compiler.yaml"
    spec_path.write_text(
        "\n".join(
            [
                "id: compiled-capsule",
                "goal:",
                "  strategy: explicit",
                "  text: Preserve the contract.",
                "checks:",
                "  - id: command",
                f"    argv: [{json.dumps(sys.executable)}, -c, 'raise SystemExit(0)']",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    loaded_spec = load_compiler_spec(spec_path)
    assert loaded_spec.goal.text == "Preserve the contract."

    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(transcript_bundle().model_dump_json(indent=2), encoding="utf-8")
    assert load_ingestion_bundle(bundle_path).provenance.source_name == "transcript.json"

    proposal = compile_proposal(transcript_bundle(), compiler_spec())
    proposal_path = tmp_path / "proposal.json"
    proposal_path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    assert load_proposal(proposal_path).proposal_id == proposal.proposal_id

    report = verify_proposal(proposal, tmp_path)
    report_path = tmp_path / "report.json"
    report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    assert load_verification_report(report_path).strong is True


def test_loaders_reject_invalid_documents(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(CapsuleCompileError, match="non-standard"):
        load_compiler_spec(bad)

    unsupported = tmp_path / "spec.txt"
    unsupported.write_text("{}", encoding="utf-8")
    with pytest.raises(CapsuleCompileError, match="must use"):
        load_compiler_spec(unsupported)

    root = tmp_path / "root.yaml"
    root.write_text("- item\n", encoding="utf-8")
    with pytest.raises(CapsuleCompileError, match="root"):
        load_compiler_spec(root)

    missing = tmp_path / "missing.json"
    with pytest.raises(CapsuleCompileError, match="unable to read"):
        load_compiler_spec(missing)


def test_explicit_goal_rejects_trace_scope() -> None:
    with pytest.raises(ValidationError, match="trace_id"):
        GoalSelector(
            strategy=GoalStrategy.EXPLICIT,
            text="Explicit goal",
            trace_id="conversation-1",
        )


def test_command_env_cannot_spoof_verification_identifiers() -> None:
    check = command_check()
    check.env = {"e2h_proposal_id": "spoof"}
    with pytest.raises(ValidationError, match="reserved"):
        CompilerSpec(id="spoof", checks=[check])


def test_correction_record_requires_matching_feedback_event() -> None:
    bundle = transcript_bundle()
    bundle.corrections[0].event_sequence = 2
    with pytest.raises(CapsuleCompileError, match="exactly one feedback"):
        compile_proposal(bundle, compiler_spec())

    bundle = transcript_bundle()
    bundle.traces[0].events[4].payload["correction_of"] = "m1"
    with pytest.raises(CapsuleCompileError, match="does not match"):
        compile_proposal(bundle, compiler_spec())


def test_materialize_rejects_stale_capsule_and_mutation_plan(tmp_path: Path) -> None:
    proposal = compile_proposal(transcript_bundle(), compiler_spec())
    approved = review_proposal(
        proposal,
        reviewer="reviewer",
        decision=ReviewDecision.APPROVE,
        timestamp=NOW,
    )
    report = verify_proposal(proposal, tmp_path)

    stale_capsule_payload = report.model_dump()
    stale_capsule_payload["capsule_sha256"] = "c" * 64
    stale_capsule = VerificationReport.model_validate(stale_capsule_payload)
    with pytest.raises(CapsuleCompileError, match="proposal capsule"):
        materialize_capsule(approved, stale_capsule)

    stale_plan_payload = report.model_dump()
    stale_plan_payload["mutation_plan_sha256"] = "d" * 64
    stale_plan = VerificationReport.model_validate(stale_plan_payload)
    with pytest.raises(CapsuleCompileError, match="mutation plan"):
        materialize_capsule(approved, stale_plan)


def test_materialize_rejects_mismatched_mutation_results(tmp_path: Path) -> None:
    proposal = compile_proposal(transcript_bundle(), compiler_spec())
    approved = review_proposal(
        proposal,
        reviewer="reviewer",
        decision=ReviewDecision.APPROVE,
        timestamp=NOW,
    )
    report = verify_proposal(proposal, tmp_path)
    report.mutations[0].mutation_id = "other"
    with pytest.raises(CapsuleCompileError, match="mutation results"):
        materialize_capsule(approved, report)


def test_materialize_rejects_results_for_other_capsule(tmp_path: Path) -> None:
    proposal = compile_proposal(transcript_bundle(), compiler_spec())
    approved = review_proposal(
        proposal,
        reviewer="reviewer",
        decision=ReviewDecision.APPROVE,
        timestamp=NOW,
    )
    report = verify_proposal(proposal, tmp_path)
    report.baseline.capsule_id = "other"
    with pytest.raises(CapsuleCompileError, match="different capsule"):
        materialize_capsule(approved, report)
