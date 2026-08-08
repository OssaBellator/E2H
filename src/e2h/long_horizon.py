"""Deterministic long-horizon constraint retention and correction benchmark."""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import load_mapping_document

_MAX_CORPUS_BYTES = 4 * 1024 * 1024
_MAX_TASKS = 1_000
_MAX_TURNS = 1_000
_MAX_PROBES = 1_000
_MAX_METADATA_BYTES = 65_536
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_KEY_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.-]{0,127}$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class LongHorizonError(ValueError):
    """Raised when a long-horizon benchmark artifact is invalid or mismatched."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must contain canonical JSON data") from exc
    return rendered.encode("utf-8")


def _validate_metadata(value: dict[str, Any], noun: str) -> dict[str, Any]:
    if len(_canonical_json_bytes(value)) > _MAX_METADATA_BYTES:
        raise ValueError(f"{noun} exceeds {_MAX_METADATA_BYTES} bytes")
    return value


class LongHorizonRole(StrEnum):
    """Visible dialogue roles in a benchmark task."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class ConstraintAction(StrEnum):
    """Private evaluator-side state transitions encoded on visible turns."""

    SET = "set"
    REVOKE = "revoke"


class ConstraintUpdate(StrictModel):
    """One private label describing a constraint introduced, corrected, or revoked by a turn."""

    id: str = Field(pattern=_ID_PATTERN)
    key: str = Field(min_length=1, max_length=128)
    action: ConstraintAction
    value: str | None = Field(default=None, max_length=1_000)
    supersedes: str | None = Field(default=None, pattern=_ID_PATTERN)

    @field_validator("key")
    @classmethod
    def key_must_be_stable(cls, value: str) -> str:
        if _KEY_PATTERN.fullmatch(value) is None:
            raise ValueError("constraint keys must use stable identifier characters")
        return value

    @model_validator(mode="after")
    def action_fields_must_be_consistent(self) -> ConstraintUpdate:
        if self.action is ConstraintAction.SET:
            if self.value is None or not self.value:
                raise ValueError("set updates require a non-empty value")
        elif self.value is not None:
            raise ValueError("revoke updates must not define a value")
        return self


class LongHorizonTurn(StrictModel):
    """One visible dialogue turn plus private machine-readable constraint labels."""

    id: str = Field(pattern=_ID_PATTERN)
    role: LongHorizonRole
    content: str = Field(min_length=1, max_length=20_000)
    updates: list[ConstraintUpdate] = Field(default_factory=list, max_length=32)


class LongHorizonProbe(StrictModel):
    """A point at which a candidate must report the currently active constraint state."""

    id: str = Field(pattern=_ID_PATTERN)
    after_turn_id: str = Field(pattern=_ID_PATTERN)
    prompt: str = Field(
        default="Report the currently active constraints using the declared constraint keys.",
        min_length=1,
        max_length=1_000,
    )


class LongHorizonTask(StrictModel):
    """One labelled long-horizon dialogue with explicit correction/revocation semantics."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=255)
    constraint_keys: list[str] = Field(min_length=1, max_length=128)
    turns: list[LongHorizonTurn] = Field(min_length=1, max_length=_MAX_TURNS)
    probes: list[LongHorizonProbe] = Field(min_length=1, max_length=_MAX_PROBES)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("constraint_keys")
    @classmethod
    def constraint_keys_must_be_unique(cls, values: list[str]) -> list[str]:
        if any(_KEY_PATTERN.fullmatch(value) is None for value in values):
            raise ValueError("constraint keys must use stable identifier characters")
        if len(values) != len(set(values)):
            raise ValueError("constraint keys must be unique")
        return values

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value, "task metadata")

    @model_validator(mode="after")
    def state_transitions_and_probes_must_be_valid(self) -> LongHorizonTask:
        turn_ids: dict[str, int] = {}
        update_ids: set[str] = set()
        active: dict[str, ConstraintUpdate] = {}
        declared_keys = set(self.constraint_keys)

        for turn_index, turn in enumerate(self.turns):
            if turn.id in turn_ids:
                raise ValueError("turn ids must be unique")
            turn_ids[turn.id] = turn_index
            for update in turn.updates:
                if update.id in update_ids:
                    raise ValueError("constraint update ids must be unique")
                if update.key not in declared_keys:
                    raise ValueError(f"constraint update uses undeclared key {update.key!r}")
                current = active.get(update.key)
                if update.action is ConstraintAction.SET:
                    if current is None:
                        if update.supersedes is not None:
                            raise ValueError(
                                "new constraint values must not supersede an inactive update"
                            )
                    elif update.supersedes != current.id:
                        raise ValueError(
                            f"changing active constraint {update.key!r} must explicitly supersede "
                            f"update {current.id!r}"
                        )
                    active[update.key] = update
                else:
                    if current is None:
                        raise ValueError(f"cannot revoke inactive constraint {update.key!r}")
                    if update.supersedes != current.id:
                        raise ValueError(
                            f"revoking active constraint {update.key!r} must supersede "
                            f"update {current.id!r}"
                        )
                    del active[update.key]
                update_ids.add(update.id)

        probe_ids: set[str] = set()
        previous_turn_index = -1
        for probe in self.probes:
            if probe.id in probe_ids:
                raise ValueError("probe ids must be unique")
            probe_turn_index = turn_ids.get(probe.after_turn_id)
            if probe_turn_index is None:
                raise ValueError("probe after_turn_id must reference a task turn")
            if probe_turn_index < previous_turn_index:
                raise ValueError("probes must be ordered by their referenced turn")
            probe_ids.add(probe.id)
            previous_turn_index = probe_turn_index
        return self


class LongHorizonCorpus(StrictModel):
    """Private labelled corpus used by the evaluator."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=255)
    tasks: list[LongHorizonTask] = Field(min_length=1, max_length=_MAX_TASKS)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value, "corpus metadata")

    @model_validator(mode="after")
    def task_ids_must_be_unique(self) -> LongHorizonCorpus:
        identifiers = [task.id for task in self.tasks]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("long-horizon task ids must be unique")
        return self


def _validated_long_horizon_corpus(corpus: LongHorizonCorpus) -> LongHorizonCorpus:
    if type(corpus) is not LongHorizonCorpus:
        raise LongHorizonError(
            f"invalid long-horizon corpus: expected LongHorizonCorpus, got {type(corpus).__name__}"
        )
    try:
        payload = corpus.model_dump(mode="python", warnings="none")
        return LongHorizonCorpus.model_validate(payload)
    except ValueError as exc:
        raise LongHorizonError(f"invalid long-horizon corpus: {exc}") from exc


class PublicLongHorizonTurn(StrictModel):
    """Visible turn exported without evaluator-side constraint labels."""

    id: str = Field(pattern=_ID_PATTERN)
    role: LongHorizonRole
    content: str = Field(min_length=1, max_length=20_000)


class PublicLongHorizonTask(StrictModel):
    """Candidate-visible task with constraint keys, dialogue, and probe positions only."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=255)
    constraint_keys: list[str] = Field(min_length=1, max_length=128)
    turns: list[PublicLongHorizonTurn] = Field(min_length=1, max_length=_MAX_TURNS)
    probes: list[LongHorizonProbe] = Field(min_length=1, max_length=_MAX_PROBES)


class PublicLongHorizonCorpus(StrictModel):
    """Label-free public export used to generate candidate predictions."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=255)
    tasks: list[PublicLongHorizonTask] = Field(min_length=1, max_length=_MAX_TASKS)


class LongHorizonProbePrediction(StrictModel):
    """Candidate-reported active constraint mapping for one probe."""

    task_id: str = Field(pattern=_ID_PATTERN)
    probe_id: str = Field(pattern=_ID_PATTERN)
    active_constraints: dict[str, str] = Field(default_factory=dict, max_length=128)

    @field_validator("active_constraints")
    @classmethod
    def active_constraints_must_be_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if _KEY_PATTERN.fullmatch(key) is None:
                raise ValueError("prediction constraint keys must use stable identifier characters")
            if not item or len(item) > 1_000:
                raise ValueError("prediction constraint values must contain 1 to 1000 characters")
        return value


class LongHorizonPredictionDocument(StrictModel):
    """Predictions bound to the exact label-free public corpus identity."""

    schema_version: Literal["0.1"] = "0.1"
    public_corpus_sha256: str = Field(pattern=_SHA256_PATTERN)
    predictions: list[LongHorizonProbePrediction] = Field(default_factory=list, max_length=100_000)

    @model_validator(mode="after")
    def prediction_pairs_must_be_unique(self) -> LongHorizonPredictionDocument:
        pairs = [(item.task_id, item.probe_id) for item in self.predictions]
        if len(pairs) != len(set(pairs)):
            raise ValueError("long-horizon prediction task/probe pairs must be unique")
        return self


def _validated_long_horizon_predictions(
    predictions: LongHorizonPredictionDocument,
) -> LongHorizonPredictionDocument:
    if type(predictions) is not LongHorizonPredictionDocument:
        raise LongHorizonError(
            "invalid long-horizon predictions: expected LongHorizonPredictionDocument, "
            f"got {type(predictions).__name__}"
        )
    try:
        payload = predictions.model_dump(mode="python", warnings="none")
        return LongHorizonPredictionDocument.model_validate(payload)
    except ValueError as exc:
        raise LongHorizonError(f"invalid long-horizon predictions: {exc}") from exc


class LongHorizonTaskScore(StrictModel):
    """Aggregate-only task score that does not reveal expected constraint labels."""

    task_id: str = Field(pattern=_ID_PATTERN)
    total: int = Field(ge=1)
    submitted: int = Field(ge=0)
    correct: int = Field(ge=0)
    score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def counts_must_be_consistent(self) -> LongHorizonTaskScore:
        if self.submitted > self.total or self.correct > self.submitted:
            raise ValueError("long-horizon task score counts are inconsistent")
        if not math.isclose(self.score, self.correct / self.total):
            raise ValueError("long-horizon task score must equal correct / total")
        return self


class LongHorizonEvaluationReport(StrictModel):
    """Aggregate evaluation report that withholds expected active constraint mappings."""

    schema_version: Literal["0.1"] = "0.1"
    corpus_id: str = Field(pattern=_ID_PATTERN)
    public_corpus_sha256: str = Field(pattern=_SHA256_PATTERN)
    total: int = Field(ge=1)
    submitted: int = Field(ge=0)
    correct: int = Field(ge=0)
    score: float = Field(ge=0, le=1)
    task_scores: list[LongHorizonTaskScore] = Field(min_length=1, max_length=_MAX_TASKS)

    @model_validator(mode="after")
    def counts_must_be_consistent(self) -> LongHorizonEvaluationReport:
        if self.submitted > self.total or self.correct > self.submitted:
            raise ValueError("long-horizon evaluation counts are inconsistent")
        if not math.isclose(self.score, self.correct / self.total):
            raise ValueError("long-horizon evaluation score must equal correct / total")
        if sum(item.total for item in self.task_scores) != self.total:
            raise ValueError("task score totals must equal report total")
        if sum(item.submitted for item in self.task_scores) != self.submitted:
            raise ValueError("task score submitted counts must equal report submitted")
        if sum(item.correct for item in self.task_scores) != self.correct:
            raise ValueError("task score correct counts must equal report correct")
        return self


def long_horizon_corpus_sha256(corpus: LongHorizonCorpus) -> str:
    """Return the private canonical identity including evaluator labels."""
    corpus = _validated_long_horizon_corpus(corpus)
    return hashlib.sha256(_canonical_json_bytes(corpus.model_dump(mode="json"))).hexdigest()


def export_public_long_horizon_corpus(corpus: LongHorizonCorpus) -> PublicLongHorizonCorpus:
    """Remove evaluator-side updates and metadata from a candidate-visible corpus."""
    corpus = _validated_long_horizon_corpus(corpus)
    return PublicLongHorizonCorpus(
        id=corpus.id,
        title=corpus.title,
        tasks=[
            PublicLongHorizonTask(
                id=task.id,
                title=task.title,
                constraint_keys=task.constraint_keys,
                turns=[
                    PublicLongHorizonTurn(id=turn.id, role=turn.role, content=turn.content)
                    for turn in task.turns
                ],
                probes=task.probes,
            )
            for task in corpus.tasks
        ],
    )


def public_long_horizon_corpus_sha256(corpus: LongHorizonCorpus) -> str:
    """Return the canonical identity of the label-free public export."""
    public = export_public_long_horizon_corpus(corpus)
    return hashlib.sha256(_canonical_json_bytes(public.model_dump(mode="json"))).hexdigest()


def _expected_probe_states(task: LongHorizonTask) -> dict[str, dict[str, str]]:
    probes_by_turn: dict[str, list[LongHorizonProbe]] = {}
    for probe in task.probes:
        probes_by_turn.setdefault(probe.after_turn_id, []).append(probe)
    state: dict[str, str] = {}
    expected: dict[str, dict[str, str]] = {}
    for turn in task.turns:
        for update in turn.updates:
            if update.action is ConstraintAction.SET:
                assert update.value is not None
                state[update.key] = update.value
            else:
                state.pop(update.key, None)
        for probe in probes_by_turn.get(turn.id, []):
            expected[probe.id] = dict(sorted(state.items()))
    return expected


def evaluate_long_horizon_predictions(
    corpus: LongHorizonCorpus,
    predictions: LongHorizonPredictionDocument,
) -> LongHorizonEvaluationReport:
    """Score exact active-constraint maps without returning hidden expected mappings."""
    corpus = _validated_long_horizon_corpus(corpus)
    predictions = _validated_long_horizon_predictions(predictions)
    expected_public_sha256 = public_long_horizon_corpus_sha256(corpus)
    if predictions.public_corpus_sha256 != expected_public_sha256:
        raise LongHorizonError("prediction public corpus digest does not match the supplied corpus")

    task_by_id = {task.id: task for task in corpus.tasks}
    valid_pairs = {(task.id, probe.id) for task in corpus.tasks for probe in task.probes}
    submitted: dict[tuple[str, str], dict[str, str]] = {}
    for prediction in predictions.predictions:
        pair = (prediction.task_id, prediction.probe_id)
        if pair not in valid_pairs:
            raise LongHorizonError(
                f"prediction references unknown task/probe pair {prediction.task_id!r}/"
                f"{prediction.probe_id!r}"
            )
        declared = set(task_by_id[prediction.task_id].constraint_keys)
        unknown_keys = sorted(set(prediction.active_constraints) - declared)
        if unknown_keys:
            raise LongHorizonError(
                f"prediction for {prediction.task_id!r}/{prediction.probe_id!r} uses "
                f"undeclared constraint keys: {', '.join(unknown_keys)}"
            )
        submitted[pair] = dict(sorted(prediction.active_constraints.items()))

    task_scores: list[LongHorizonTaskScore] = []
    total = 0
    correct = 0
    for task in corpus.tasks:
        expected = _expected_probe_states(task)
        task_correct = 0
        task_submitted = 0
        for probe in task.probes:
            total += 1
            pair = (task.id, probe.id)
            candidate = submitted.get(pair)
            if candidate is None:
                continue
            task_submitted += 1
            if candidate == expected[probe.id]:
                task_correct += 1
                correct += 1
        task_scores.append(
            LongHorizonTaskScore(
                task_id=task.id,
                total=len(task.probes),
                submitted=task_submitted,
                correct=task_correct,
                score=task_correct / len(task.probes),
            )
        )
    return LongHorizonEvaluationReport(
        corpus_id=corpus.id,
        public_corpus_sha256=expected_public_sha256,
        total=total,
        submitted=len(submitted),
        correct=correct,
        score=correct / total,
        task_scores=task_scores,
    )


def load_long_horizon_corpus(path: Path) -> LongHorizonCorpus:
    """Load one bounded strict JSON/YAML labelled long-horizon corpus."""
    try:
        payload = load_mapping_document(
            path,
            noun="long-horizon corpus",
            max_bytes=_MAX_CORPUS_BYTES,
        )
        return LongHorizonCorpus.model_validate(payload)
    except ValueError as exc:
        if isinstance(exc, LongHorizonError):
            raise
        raise LongHorizonError(f"invalid long-horizon corpus: {exc}") from exc


def load_long_horizon_predictions(path: Path) -> LongHorizonPredictionDocument:
    """Load one bounded strict JSON prediction document."""
    if path.suffix.lower() != ".json":
        raise LongHorizonError("long-horizon predictions must use .json")
    try:
        payload = load_mapping_document(
            path,
            noun="prediction",
            max_bytes=_MAX_CORPUS_BYTES,
        )
        return LongHorizonPredictionDocument.model_validate(payload)
    except ValueError as exc:
        if isinstance(exc, LongHorizonError):
            raise
        raise LongHorizonError(f"invalid long-horizon predictions: {exc}") from exc
