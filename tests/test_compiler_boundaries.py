from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import BaseModel, ConfigDict

import e2h.compiler as compiler
from e2h.compiler import (
    CapsuleCompileError,
    CapsuleProposal,
    CompilerSpec,
    GoalSelector,
    GoalStrategy,
    ReviewDecision,
    VerificationReport,
    compile_proposal,
    materialize_capsule,
    review_proposal,
    verify_proposal,
)
from e2h.ingest import EvidenceFormat, IngestionBundle, SourceProvenance
from e2h.runner import RunResult, RunStatus
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


class _BundleSubclass(IngestionBundle):
    pass


class _SpecSubclass(CompilerSpec):
    pass


class _ProposalSubclass(CapsuleProposal):
    pass


class _ReportSubclass(VerificationReport):
    pass


class _Lookalike(BaseModel):
    model_config = ConfigDict(extra="allow")


def bundle() -> IngestionBundle:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    context = TraceContext(run_id="compiler-boundary", capsule_id="evidence")
    return IngestionBundle(
        provenance=SourceProvenance(
            format=EvidenceFormat.TRANSCRIPT_JSON,
            source_name="boundary.json",
            sha256="1" * 64,
            size_bytes=128,
            redaction_enabled=True,
        ),
        traces=[
            Trace(
                trace_id="compiler-boundary",
                events=[
                    TraceEvent(
                        trace_id="compiler-boundary",
                        sequence=0,
                        event_type=TraceEventType.RUN_STARTED,
                        timestamp=started,
                        context=context,
                    ),
                    TraceEvent(
                        trace_id="compiler-boundary",
                        sequence=1,
                        event_type=TraceEventType.RUN_COMPLETED,
                        timestamp=started + timedelta(seconds=1),
                        context=context,
                    ),
                ],
            )
        ],
    )


def spec() -> CompilerSpec:
    return CompilerSpec(
        id="compiler-boundary",
        goal=GoalSelector(
            strategy=GoalStrategy.EXPLICIT,
            text="Run the compiler boundary check.",
        ),
        checks=[
            {
                "id": "check",
                "argv": [
                    sys.executable,
                    "-c",
                    "import os,sys; sys.exit(1 if os.getenv('FAIL') else 0)",
                ],
            }
        ],
        mutations=[
            {
                "id": "fail-check",
                "env": {"FAIL": "1"},
                "check_ids": ["check"],
            }
        ],
    )


def proposal() -> CapsuleProposal:
    return compile_proposal(bundle(), spec())


def strong_report(tmp_path: Path, source: CapsuleProposal | None = None) -> VerificationReport:
    return verify_proposal(source or proposal(), tmp_path)


def approved_proposal(source: CapsuleProposal | None = None) -> CapsuleProposal:
    return review_proposal(
        source or proposal(),
        reviewer="boundary-reviewer",
        decision=ReviewDecision.APPROVE,
    )


def passing_result(capsule_id: str, *, passed: bool) -> RunResult:
    now = datetime.now(UTC)
    return RunResult(
        capsule_id=capsule_id,
        status=RunStatus.PASSED if passed else RunStatus.FAILED,
        started_at=now,
        finished_at=now,
        duration_seconds=0,
        checks=[],
    )


def test_compile_rejects_bundle_and_spec_subclasses_and_lookalikes() -> None:
    source = bundle()
    current_spec = spec()
    bundle_subclass = _BundleSubclass.model_validate(source.model_dump(mode="json"))
    spec_subclass = _SpecSubclass.model_validate(current_spec.model_dump(mode="json"))
    bundle_lookalike = _Lookalike.model_validate(source.model_dump(mode="json"))

    with pytest.raises(
        CapsuleCompileError,
        match="expected IngestionBundle, got _BundleSubclass",
    ):
        compile_proposal(bundle_subclass, current_spec)

    with pytest.raises(
        CapsuleCompileError,
        match="expected CompilerSpec, got _SpecSubclass",
    ):
        compile_proposal(source, spec_subclass)

    with pytest.raises(
        CapsuleCompileError,
        match="expected IngestionBundle, got _Lookalike",
    ):
        compile_proposal(cast(Any, bundle_lookalike), current_spec)


def test_compile_revalidates_mutated_compiler_spec() -> None:
    current_spec = spec()
    current_spec.checks.append(current_spec.checks[0].model_copy(deep=True))

    with pytest.raises(CapsuleCompileError, match="check and oracle ids must be unique"):
        compile_proposal(bundle(), current_spec)


def test_compile_revalidates_reserved_environment_mutation() -> None:
    current_spec = spec()
    current_spec.checks[0].env["E2H_MUTATION_ID"] = "spoofed"

    with pytest.raises(CapsuleCompileError, match="reserved E2H mutation identifiers"):
        compile_proposal(bundle(), current_spec)


def test_compile_preserves_canonical_invalid_metadata() -> None:
    current_spec = spec()
    current_spec.metadata = {"invalid": {"set-value"}}

    with pytest.raises(CapsuleCompileError, match="compiler metadata must be JSON-serializable"):
        compile_proposal(bundle(), current_spec)


def test_compile_revalidates_mutated_ingestion_trace() -> None:
    source = bundle()
    source.traces[0].events[1].sequence = 3

    with pytest.raises(CapsuleCompileError, match="invalid ingestion bundle"):
        compile_proposal(source, spec())


def test_verify_and_review_reject_proposal_subclasses_and_lookalikes(tmp_path: Path) -> None:
    source = proposal()
    subclassed = _ProposalSubclass.model_validate(source.model_dump(mode="json"))
    lookalike = _Lookalike.model_validate(source.model_dump(mode="json"))

    with pytest.raises(
        CapsuleCompileError,
        match="expected CapsuleProposal, got _ProposalSubclass",
    ):
        verify_proposal(subclassed, tmp_path)

    with pytest.raises(
        CapsuleCompileError,
        match="expected CapsuleProposal, got _Lookalike",
    ):
        review_proposal(
            cast(Any, lookalike),
            reviewer="boundary-reviewer",
            decision=ReviewDecision.APPROVE,
        )


def test_verify_and_review_revalidate_mutated_proposal_core(tmp_path: Path) -> None:
    source = proposal()
    source.core.capsule.goal = ""

    with pytest.raises(CapsuleCompileError, match="invalid capsule proposal"):
        verify_proposal(source, tmp_path)

    with pytest.raises(CapsuleCompileError, match="invalid capsule proposal"):
        review_proposal(
            source,
            reviewer="boundary-reviewer",
            decision=ReviewDecision.APPROVE,
        )


def test_materialize_rejects_proposal_and_report_subclasses(tmp_path: Path) -> None:
    source = proposal()
    approved = approved_proposal(source)
    report = strong_report(tmp_path, source)
    proposal_subclass = _ProposalSubclass.model_validate(approved.model_dump(mode="json"))
    report_subclass = _ReportSubclass.model_validate(report.model_dump(mode="json"))

    with pytest.raises(
        CapsuleCompileError,
        match="expected CapsuleProposal, got _ProposalSubclass",
    ):
        materialize_capsule(proposal_subclass, report)

    with pytest.raises(
        CapsuleCompileError,
        match="expected VerificationReport, got _ReportSubclass",
    ):
        materialize_capsule(approved, report_subclass)


def test_materialize_revalidates_mutated_verification_summary(tmp_path: Path) -> None:
    source = proposal()
    approved = approved_proposal(source)
    report = strong_report(tmp_path, source)
    assert report.strong is True
    report.strong = False

    with pytest.raises(CapsuleCompileError, match="strong does not match verification results"):
        materialize_capsule(approved, report)


def test_boundaries_normalize_warning_prone_raw_nested_assignments(tmp_path: Path) -> None:
    source_bundle = bundle()
    source_bundle.traces = [source_bundle.traces[0].model_dump(mode="json")]
    source_spec = spec()
    source_spec.checks = [source_spec.checks[0].model_dump(mode="json")]
    source = compile_proposal(source_bundle, source_spec)

    source.core = source.core.model_dump(mode="json")
    report = verify_proposal(source, tmp_path)
    approved = review_proposal(
        source,
        reviewer="boundary-reviewer",
        decision=ReviewDecision.APPROVE,
    )
    report.mutations = [item.model_dump(mode="json") for item in report.mutations]

    materialized = materialize_capsule(approved, report)

    assert materialized.id == "compiler-boundary"


def test_verify_uses_detached_proposal_snapshot_during_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = proposal()
    original_id = source.core.capsule.id
    original_proposal_id = source.proposal_id
    observed_env: list[dict[str, str]] = []

    def fake_run_capsule(
        capsule: Any,
        workspace: Path,
        **kwargs: Any,
    ) -> RunResult:
        del workspace, kwargs
        observed_env.append(dict(capsule.success.commands[0].env))
        if len(observed_env) == 1:
            source.core.capsule.id = "caller-mutated-capsule"
            source.core.mutations[0].env = {"FAIL": "caller-mutated"}
            return passing_result(capsule.id, passed=True)
        return passing_result(capsule.id, passed=False)

    monkeypatch.setattr(compiler, "run_capsule", fake_run_capsule)

    report = verify_proposal(source, tmp_path)

    assert report.proposal_id == original_proposal_id
    assert report.baseline.capsule_id == original_id
    assert report.mutations[0].result.capsule_id == original_id
    assert observed_env[1]["FAIL"] == "1"
    assert observed_env[1]["E2H_MUTATION_ID"] == "fail-check"
