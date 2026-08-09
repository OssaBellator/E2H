"""Review-gated compilation of sanitized evidence into executable task capsules."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import _validate_json_compatible, load_mapping_document
from e2h.ingest import IngestionBundle, SourceProvenance
from e2h.models import (
    AllowedActions,
    CommandCheck,
    ContainerSandbox,
    ExecutionLimits,
    InitialState,
    SuccessSpec,
    TaskCapsule,
)
from e2h.oracles import (
    ORACLE_MUTATION_ENV,
    OracleTemplate,
    compile_oracle,
    oracle_mutation_id,
    oracle_mutation_operator,
)
from e2h.runner import ExecutionBackend, RunResult, RunStatus, run_capsule
from e2h.snapshot import SnapshotReference
from e2h.trace import TraceEvent, TraceEventType

_MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
_MAX_MUTATIONS = 100
_RESERVED_MUTATION_ENV = frozenset({"E2H_MUTATION_ID", "E2H_PROPOSAL_ID"})
_RESERVED_CHECK_ENV = _RESERVED_MUTATION_ENV | {ORACLE_MUTATION_ENV}
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class CapsuleCompileError(ValueError):
    """Raised when evidence cannot be compiled or promoted safely."""


class GoalStrategy(StrEnum):
    """Deterministic strategies for selecting a proposal goal."""

    LATEST_CORRECTION = "latest_correction"
    LATEST_USER_MESSAGE = "latest_user_message"
    EXPLICIT = "explicit"


class ReviewDecision(StrEnum):
    """Human decisions attached to an immutable proposal core."""

    APPROVE = "approve"
    REJECT = "reject"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_InputModelT = TypeVar("_InputModelT", bound=BaseModel)


def _revalidate_compiler_model(
    value: BaseModel,
    model_type: type[_InputModelT],
    *,
    noun: str,
) -> _InputModelT:
    """Return a detached compiler-stage input after enforcing one concrete model type."""
    if type(value) is not model_type:
        raise CapsuleCompileError(
            f"invalid {noun}: expected {model_type.__name__}, got {type(value).__name__}"
        )
    try:
        payload = value.model_dump(mode="python", warnings="none")
        return model_type.model_validate(payload)
    except ValueError as exc:
        raise CapsuleCompileError(f"invalid {noun}: {exc}") from exc


class GoalSelector(StrictModel):
    """Select a goal from sanitized evidence or explicit trusted text."""

    strategy: GoalStrategy = GoalStrategy.LATEST_CORRECTION
    text: str | None = Field(default=None, max_length=10_000)
    trace_id: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def strategy_fields_must_match(self) -> GoalSelector:
        if self.strategy is GoalStrategy.EXPLICIT:
            if not self.text:
                raise ValueError("explicit goal strategy requires non-empty text")
            if self.trace_id is not None:
                raise ValueError("trace_id is only valid with an evidence-derived goal strategy")
        elif self.text is not None:
            raise ValueError("goal text is only valid with the explicit strategy")
        return self


class EnvironmentMutation(StrictModel):
    """A controlled environment perturbation expected to make the oracle fail."""

    id: str = Field(pattern=_ID_PATTERN)
    description: str | None = Field(default=None, max_length=2_000)
    env: dict[str, str] = Field(min_length=1, max_length=100)
    check_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("env")
    @classmethod
    def environment_must_be_safe(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not key or "=" in key or "\x00" in key:
                raise ValueError(
                    "environment keys must be non-empty and contain neither '=' nor NUL"
                )
            if key.upper() in _RESERVED_MUTATION_ENV:
                raise ValueError(
                    "environment keys must not override reserved E2H mutation identifiers"
                )
            if "\x00" in item:
                raise ValueError("environment values must not contain NUL")
        return value

    @field_validator("check_ids")
    @classmethod
    def check_ids_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("mutation check_ids must be unique")
        return value


def _generated_oracle_mutations(
    oracles: list[OracleTemplate],
) -> list[EnvironmentMutation]:
    return [
        EnvironmentMutation(
            id=oracle_mutation_id(oracle),
            description=f"Mutate {oracle.kind} oracle {oracle.id}",
            env={ORACLE_MUTATION_ENV: oracle_mutation_operator(oracle)},
            check_ids=[oracle.id],
        )
        for oracle in oracles
    ]


class CompilerSpec(StrictModel):
    """Human-authored constraints for producing a reviewable capsule proposal."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    goal: GoalSelector = Field(default_factory=GoalSelector)
    initial_state: InitialState = Field(default_factory=InitialState)
    allowed_actions: AllowedActions = Field(default_factory=AllowedActions)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    sandbox: ContainerSandbox | None = None
    checks: list[CommandCheck] = Field(default_factory=list, max_length=1000)
    oracles: list[OracleTemplate] = Field(default_factory=list, max_length=100)
    snapshots: list[SnapshotReference] = Field(default_factory=list, max_length=100)
    auto_mutate_oracles: bool = True
    mutations: list[EnvironmentMutation] = Field(default_factory=list, max_length=_MAX_MUTATIONS)
    allow_unredacted: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def checks_and_mutations_must_be_consistent(self) -> CompilerSpec:
        _ensure_json(self.metadata, "compiler metadata")
        snapshot_ids = [snapshot.snapshot_id for snapshot in self.snapshots]
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("snapshot ids must be unique")
        compiled_oracles = [compile_oracle(oracle) for oracle in self.oracles]
        check_ids = [check.id for check in self.checks] + [check.id for check in compiled_oracles]
        if not check_ids:
            raise ValueError("at least one command check or oracle is required")
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("check and oracle ids must be unique")
        generated_mutations = (
            _generated_oracle_mutations(self.oracles) if self.auto_mutate_oracles else []
        )
        all_mutations = [*self.mutations, *generated_mutations]
        if len(all_mutations) > _MAX_MUTATIONS:
            raise ValueError(f"combined mutations exceeds {_MAX_MUTATIONS}")
        mutation_ids = [mutation.id for mutation in all_mutations]
        if len(mutation_ids) != len(set(mutation_ids)):
            raise ValueError("mutation ids must be unique")
        known = set(check_ids)
        for check in self.checks:
            reserved = sorted(key for key in check.env if key.upper() in _RESERVED_CHECK_ENV)
            if reserved:
                raise ValueError(
                    "command environments must not override reserved E2H mutation identifiers: "
                    + ", ".join(reserved)
                )
        for mutation in all_mutations:
            missing = sorted(set(mutation.check_ids) - known)
            if missing:
                raise ValueError(f"mutation references unknown checks: {', '.join(missing)}")
        if len(check_ids) > self.limits.max_commands:
            raise ValueError("checks and oracles exceeds limits.max_commands")
        return self


class EvidenceReference(StrictModel):
    """Content-addressed pointer to one observable event used by the compiler."""

    trace_id: str
    sequence: int = Field(ge=0)
    event_type: TraceEventType
    event_sha256: str = Field(pattern=_SHA256_PATTERN)
    message_id: str | None = None
    role: str | None = None


class ProposalCore(StrictModel):
    """Immutable proposal content covered by proposal_id."""

    compiler_version: Literal["0.1"] = "0.1"
    capsule: TaskCapsule
    provenance: SourceProvenance
    bundle_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence: list[EvidenceReference] = Field(default_factory=list, max_length=100)
    mutations: list[EnvironmentMutation] = Field(default_factory=list, max_length=_MAX_MUTATIONS)
    warnings: list[str] = Field(default_factory=list, max_length=100)


class ReviewRecord(StrictModel):
    """Human review decision bound to one immutable proposal ID."""

    proposal_id: str = Field(pattern=_SHA256_PATTERN)
    reviewer: str = Field(min_length=1, max_length=255)
    decision: ReviewDecision
    timestamp: datetime
    note: str | None = Field(default=None, max_length=10_000)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review timestamp must be timezone-aware")
        return value


class CapsuleProposal(StrictModel):
    """Reviewable capsule proposal with an immutable, content-addressed core."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: str = Field(pattern=_SHA256_PATTERN)
    core: ProposalCore
    reviews: list[ReviewRecord] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def proposal_id_and_reviews_must_match(self) -> CapsuleProposal:
        expected = proposal_digest(self.core)
        if self.proposal_id != expected:
            raise ValueError("proposal_id does not match the immutable proposal core")
        if any(review.proposal_id != self.proposal_id for review in self.reviews):
            raise ValueError("all reviews must reference the proposal_id")
        return self

    @property
    def review_decision(self) -> ReviewDecision | None:
        return self.reviews[-1].decision if self.reviews else None

    @property
    def approved(self) -> bool:
        return self.review_decision is ReviewDecision.APPROVE


class MutationResult(StrictModel):
    """Result of executing one declared mutation probe."""

    mutation_id: str
    detected: bool
    result: RunResult


def capsule_digest(capsule: TaskCapsule) -> str:
    """Return the stable digest of the exact capsule covered by verification."""
    return _digest(capsule.model_dump(mode="json"))


def mutation_plan_digest(mutations: list[EnvironmentMutation]) -> str:
    """Return the stable digest of the ordered mutation plan."""
    return _digest([mutation.model_dump(mode="json") for mutation in mutations])


class VerificationReport(StrictModel):
    """Baseline and mutation evidence used to gate capsule materialization."""

    schema_version: Literal["0.1"] = "0.1"
    proposal_id: str = Field(pattern=_SHA256_PATTERN)
    capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    mutation_plan_sha256: str = Field(pattern=_SHA256_PATTERN)
    verified_at: datetime
    baseline: RunResult
    mutations: list[MutationResult]
    baseline_passed: bool
    all_mutations_detected: bool
    strong: bool

    @field_validator("verified_at")
    @classmethod
    def verified_at_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("verified_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def summary_fields_must_match_results(self) -> VerificationReport:
        baseline_passed = self.baseline.status is RunStatus.PASSED
        all_detected = bool(self.mutations) and all(item.detected for item in self.mutations)
        if self.baseline_passed != baseline_passed:
            raise ValueError("baseline_passed does not match baseline status")
        if self.all_mutations_detected != all_detected:
            raise ValueError("all_mutations_detected does not match mutation results")
        if self.strong != (baseline_passed and all_detected):
            raise ValueError("strong does not match verification results")
        return self


def _ensure_json(value: Any, noun: str) -> None:
    try:
        _validate_json_compatible(value)
    except ValueError as exc:
        raise ValueError(f"{noun} must be JSON-serializable") from exc


def _canonical_json(value: Any) -> bytes:
    _validate_json_compatible(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def proposal_digest(core: ProposalCore) -> str:
    """Return the stable ID covering an immutable proposal core."""
    return _digest(core.model_dump(mode="json"))


def bundle_digest(bundle: IngestionBundle) -> str:
    """Return a stable content digest for a normalized ingestion bundle."""
    return _digest(bundle.model_dump(mode="json"))


def _event_reference(event: TraceEvent) -> EvidenceReference:
    return EvidenceReference(
        trace_id=event.trace_id,
        sequence=event.sequence,
        event_type=event.event_type,
        event_sha256=_digest(event.model_dump(mode="json")),
        message_id=_optional_text(event.attributes.get("message_id")),
        role=_optional_text(event.attributes.get("role")),
    )


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _message_events(bundle: IngestionBundle, trace_id: str | None) -> list[TraceEvent]:
    return [
        event
        for trace in bundle.traces
        if trace_id is None or trace.trace_id == trace_id
        for event in trace.events
        if event.event_type is TraceEventType.MESSAGE_OBSERVED
    ]


def _message_by_id(events: list[TraceEvent], trace_id: str, message_id: str) -> TraceEvent:
    matches = [
        event
        for event in events
        if event.trace_id == trace_id and event.attributes.get("message_id") == message_id
    ]
    if len(matches) != 1:
        raise CapsuleCompileError(
            f"expected exactly one message event for {trace_id}/{message_id}, found {len(matches)}"
        )
    return matches[0]


def _message_content(event: TraceEvent) -> str:
    content = event.payload.get("content")
    if not isinstance(content, str) or not content:
        raise CapsuleCompileError(
            f"message event {event.trace_id}/{event.sequence} has no non-empty content"
        )
    if len(content) > 10_000:
        raise CapsuleCompileError("selected goal exceeds the capsule goal limit")
    return content


def _select_goal(
    bundle: IngestionBundle,
    selector: GoalSelector,
) -> tuple[str, list[EvidenceReference]]:
    if selector.trace_id is not None and not any(
        trace.trace_id == selector.trace_id for trace in bundle.traces
    ):
        raise CapsuleCompileError(f"selected trace does not exist: {selector.trace_id}")

    if selector.strategy is GoalStrategy.EXPLICIT:
        assert selector.text is not None
        return selector.text, []

    messages = _message_events(bundle, selector.trace_id)
    if selector.strategy is GoalStrategy.LATEST_USER_MESSAGE:
        user_messages = [event for event in messages if event.attributes.get("role") == "user"]
        if not user_messages:
            raise CapsuleCompileError("no user message is available for goal selection")
        selected = max(
            user_messages,
            key=lambda event: (event.timestamp, event.trace_id, event.sequence),
        )
        return _message_content(selected), [_event_reference(selected)]

    corrections = [
        correction
        for correction in bundle.corrections
        if selector.trace_id is None or correction.trace_id == selector.trace_id
    ]
    if not corrections:
        raise CapsuleCompileError("no explicit correction is available for goal selection")
    correction_candidates: list[tuple[TraceEvent, TraceEvent, TraceEvent]] = []
    for correction in corrections:
        correction_message = _message_by_id(messages, correction.trace_id, correction.message_id)
        corrected_message = _message_by_id(messages, correction.trace_id, correction.correction_of)
        feedback_matches = [
            event
            for trace in bundle.traces
            if trace.trace_id == correction.trace_id
            for event in trace.events
            if event.sequence == correction.event_sequence
            and event.event_type is TraceEventType.FEEDBACK_OBSERVED
        ]
        if len(feedback_matches) != 1:
            raise CapsuleCompileError("correction record must reference exactly one feedback event")
        feedback = feedback_matches[0]
        if (
            feedback.payload.get("message_id") != correction.message_id
            or feedback.payload.get("correction_of") != correction.correction_of
        ):
            raise CapsuleCompileError("correction record does not match feedback event payload")
        correction_candidates.append((correction_message, corrected_message, feedback))
    correction_message, corrected_message, feedback = max(
        correction_candidates,
        key=lambda item: (item[0].timestamp, item[0].trace_id, item[0].sequence),
    )
    references = [_event_reference(corrected_message), _event_reference(correction_message)]
    references.append(_event_reference(feedback))
    return _message_content(correction_message), references


def compile_proposal(bundle: IngestionBundle, spec: CompilerSpec) -> CapsuleProposal:
    """Compile sanitized evidence and trusted check declarations into a draft proposal."""
    bundle = _revalidate_compiler_model(bundle, IngestionBundle, noun="ingestion bundle")
    spec = _revalidate_compiler_model(spec, CompilerSpec, noun="compiler specification")
    compiled_checks = [*spec.checks, *(compile_oracle(oracle) for oracle in spec.oracles)]
    compiled_mutations = [
        *spec.mutations,
        *(_generated_oracle_mutations(spec.oracles) if spec.auto_mutate_oracles else []),
    ]
    if not bundle.provenance.redaction_enabled and not spec.allow_unredacted:
        raise CapsuleCompileError(
            "bundle is unredacted; set allow_unredacted only for an intentional trusted workflow"
        )
    goal, references = _select_goal(bundle, spec.goal)
    source_digest = bundle_digest(bundle)
    metadata = {
        **spec.metadata,
        "e2h_compiler": {
            "version": "0.1",
            "bundle_sha256": source_digest,
            "source_sha256": bundle.provenance.sha256,
            "source_format": bundle.provenance.format.value,
            "evidence": [reference.model_dump(mode="json") for reference in references],
            "oracles": [oracle.model_dump(mode="json") for oracle in spec.oracles],
            "snapshots": [snapshot.model_dump(mode="json") for snapshot in spec.snapshots],
        },
    }
    capsule = TaskCapsule(
        id=spec.id,
        goal=goal,
        initial_state=spec.initial_state,
        allowed_actions=spec.allowed_actions,
        limits=spec.limits,
        sandbox=spec.sandbox,
        success=SuccessSpec(commands=compiled_checks),
        metadata=metadata,
    )
    warnings: list[str] = []
    if not compiled_mutations:
        warnings.append("no mutation probes are declared; strong verification cannot pass")
    if not bundle.provenance.redaction_enabled:
        warnings.append("proposal was compiled from an explicitly allowed unredacted bundle")
    if spec.goal.strategy is GoalStrategy.EXPLICIT:
        warnings.append("goal text is explicit and is not derived from an evidence event")
    core = ProposalCore(
        capsule=capsule,
        provenance=bundle.provenance,
        bundle_sha256=source_digest,
        evidence=references,
        mutations=compiled_mutations,
        warnings=warnings,
    )
    return CapsuleProposal(proposal_id=proposal_digest(core), core=core)


def _mutated_capsule(
    proposal: CapsuleProposal,
    mutation: EnvironmentMutation,
) -> TaskCapsule:
    capsule = proposal.core.capsule.model_copy(deep=True)
    targets = set(mutation.check_ids) or {check.id for check in capsule.success.commands}
    injected = {
        **mutation.env,
        "E2H_MUTATION_ID": mutation.id,
        "E2H_PROPOSAL_ID": proposal.proposal_id,
    }
    for check in capsule.success.commands:
        if check.id in targets:
            check.env = {**check.env, **injected}
    return capsule


def verify_proposal(
    proposal: CapsuleProposal,
    workspace: Path,
    *,
    backend: ExecutionBackend = ExecutionBackend.AUTO,
    container_runtime: str | None = None,
) -> VerificationReport:
    """Run the baseline capsule and ensure each controlled mutation is detected."""
    proposal = _revalidate_compiler_model(proposal, CapsuleProposal, noun="capsule proposal")
    baseline = run_capsule(
        proposal.core.capsule,
        workspace,
        backend=backend,
        container_runtime=container_runtime,
    )
    mutation_results: list[MutationResult] = []
    for mutation in proposal.core.mutations:
        result = run_capsule(
            _mutated_capsule(proposal, mutation),
            workspace,
            backend=backend,
            container_runtime=container_runtime,
        )
        mutation_results.append(
            MutationResult(
                mutation_id=mutation.id,
                detected=result.status is not RunStatus.PASSED,
                result=result,
            )
        )
    baseline_passed = baseline.status is RunStatus.PASSED
    all_detected = bool(mutation_results) and all(item.detected for item in mutation_results)
    return VerificationReport(
        proposal_id=proposal.proposal_id,
        capsule_sha256=capsule_digest(proposal.core.capsule),
        mutation_plan_sha256=mutation_plan_digest(proposal.core.mutations),
        verified_at=datetime.now(UTC),
        baseline=baseline,
        mutations=mutation_results,
        baseline_passed=baseline_passed,
        all_mutations_detected=all_detected,
        strong=baseline_passed and all_detected,
    )


def review_proposal(
    proposal: CapsuleProposal,
    *,
    reviewer: str,
    decision: ReviewDecision,
    note: str | None = None,
    timestamp: datetime | None = None,
) -> CapsuleProposal:
    """Append a human review decision without changing the immutable proposal core."""
    proposal = _revalidate_compiler_model(proposal, CapsuleProposal, noun="capsule proposal")
    updated = proposal.model_copy(deep=True)
    updated.reviews.append(
        ReviewRecord(
            proposal_id=proposal.proposal_id,
            reviewer=reviewer,
            decision=decision,
            timestamp=timestamp or datetime.now(UTC),
            note=note,
        )
    )
    return CapsuleProposal.model_validate(updated.model_dump(mode="python", warnings="none"))


def materialize_capsule(
    proposal: CapsuleProposal,
    report: VerificationReport,
    *,
    require_approved: bool = True,
    require_strong: bool = True,
) -> TaskCapsule:
    """Return the executable capsule only after matching review and verification gates."""
    proposal = _revalidate_compiler_model(proposal, CapsuleProposal, noun="capsule proposal")
    report = _revalidate_compiler_model(report, VerificationReport, noun="verification report")
    if report.proposal_id != proposal.proposal_id:
        raise CapsuleCompileError("verification report does not match the proposal")
    if report.capsule_sha256 != capsule_digest(proposal.core.capsule):
        raise CapsuleCompileError("verification report does not cover the proposal capsule")
    if report.mutation_plan_sha256 != mutation_plan_digest(proposal.core.mutations):
        raise CapsuleCompileError("verification report does not cover the proposal mutation plan")
    expected_mutation_ids = [mutation.id for mutation in proposal.core.mutations]
    if [item.mutation_id for item in report.mutations] != expected_mutation_ids:
        raise CapsuleCompileError("verification report mutation results do not match the proposal")
    if report.baseline.capsule_id != proposal.core.capsule.id or any(
        item.result.capsule_id != proposal.core.capsule.id for item in report.mutations
    ):
        raise CapsuleCompileError("verification report contains results for a different capsule")
    if require_approved and not proposal.approved:
        raise CapsuleCompileError("proposal is not approved by its latest review")
    if require_strong and not report.strong:
        raise CapsuleCompileError("proposal did not pass strong baseline and mutation verification")
    return proposal.core.capsule.model_copy(deep=True)


def _read_document(path: Path) -> Any:
    try:
        return load_mapping_document(path, noun="document", max_bytes=_MAX_DOCUMENT_BYTES)
    except ValueError as exc:
        raise CapsuleCompileError(str(exc)) from exc


def _load_model(path: Path, model: type[BaseModel], noun: str) -> BaseModel:
    data = _read_document(path)
    if not isinstance(data, dict):
        raise CapsuleCompileError(f"{noun} root must be an object")
    try:
        return model.model_validate(data)
    except ValueError as exc:
        raise CapsuleCompileError(f"invalid {noun}: {exc}") from exc


def load_compiler_spec(path: Path) -> CompilerSpec:
    return CompilerSpec.model_validate(_load_model(path, CompilerSpec, "compiler specification"))


def load_ingestion_bundle(path: Path) -> IngestionBundle:
    return IngestionBundle.model_validate(_load_model(path, IngestionBundle, "ingestion bundle"))


def load_proposal(path: Path) -> CapsuleProposal:
    return CapsuleProposal.model_validate(_load_model(path, CapsuleProposal, "capsule proposal"))


def load_verification_report(path: Path) -> VerificationReport:
    return VerificationReport.model_validate(
        _load_model(path, VerificationReport, "verification report")
    )
