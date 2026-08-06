"""Replay-matrix models and execution for comparing harness variants."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from statistics import fmean
from time import monotonic
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.models import TaskCapsule
from e2h.runner import ExecutionBackend, RunResult, RunStatus, run_capsule
from e2h.trace import Trace, trace_from_run_result

_RESERVED_VARIANT_ENV = frozenset({"E2H_VARIANT_ID", "E2H_REPETITION"})
_MAX_TRACE_ID_LENGTH = 256


def _validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("path must be relative")
    if ".." in path.parts:
        raise ValueError("path must not contain parent traversal")
    return value


def _matrix_run_id(experiment_id: str, variant_id: str, repetition: int) -> str:
    natural = f"{experiment_id}.{variant_id}.{repetition:03d}"
    if len(natural) <= _MAX_TRACE_ID_LENGTH:
        return natural
    digest = hashlib.sha256(natural.encode("utf-8")).hexdigest()[:16]
    prefix_length = _MAX_TRACE_ID_LENGTH - len(digest) - 1
    return f"{natural[:prefix_length]}.{digest}"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HarnessVariant(StrictModel):
    """One harness configuration represented by deterministic environment overrides."""

    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
    env: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("env")
    @classmethod
    def environment_must_be_process_safe(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not key or "=" in key or "\x00" in key:
                raise ValueError(
                    "environment keys must be non-empty and contain neither '=' nor NUL"
                )
            if key.upper() in _RESERVED_VARIANT_ENV:
                raise ValueError("environment keys must not override reserved E2H slot identifiers")
            if "\x00" in item:
                raise ValueError("environment values must not contain NUL")
        return value


class ExperimentSpec(StrictModel):
    """Declarative Cartesian replay matrix for one capsule."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
    capsule: str
    workspace: str = "."
    repetitions: int = Field(default=1, ge=1, le=100)
    variants: list[HarnessVariant] = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capsule", "workspace")
    @classmethod
    def paths_must_be_safe(cls, value: str) -> str:
        return _validate_relative_path(value)

    @model_validator(mode="after")
    def matrix_must_be_bounded_and_unique(self) -> ExperimentSpec:
        variant_ids = [variant.id for variant in self.variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError("variant ids must be unique")
        if len(self.variants) * self.repetitions > 1000:
            raise ValueError("experiment matrix must not exceed 1000 runs")
        return self


class ExperimentRun(StrictModel):
    """One matrix cell execution."""

    run_id: str
    variant_id: str
    repetition: int = Field(ge=0)
    trace_id: str
    result: RunResult


class VariantSummary(StrictModel):
    """Aggregated reliability and latency for one variant."""

    variant_id: str
    runs: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    mean_duration_seconds: float = Field(ge=0)


class ExperimentResult(StrictModel):
    """Structured output of a complete replay matrix."""

    schema_version: Literal["0.1"] = "0.1"
    experiment_id: str
    capsule_id: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(ge=0)
    runs: list[ExperimentRun] = Field(min_length=1)
    summaries: list[VariantSummary] = Field(min_length=1)

    @property
    def all_passed(self) -> bool:
        return all(run.result.status is RunStatus.PASSED for run in self.runs)


class ExperimentExecution(StrictModel):
    """In-memory result plus normalized trace evidence."""

    result: ExperimentResult
    traces: list[Trace]


def resolve_under_root(root: Path, relative: str) -> Path:
    """Resolve a declared path and reject filesystem or symlink escape."""
    root_path = root.resolve()
    candidate = (root_path / relative).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ValueError(f"path escapes experiment root: {relative}") from exc
    return candidate


def _variant_capsule(
    capsule: TaskCapsule,
    variant: HarnessVariant,
    repetition: int,
) -> TaskCapsule:
    copy = capsule.model_copy(deep=True)
    injected = {
        **variant.env,
        "E2H_VARIANT_ID": variant.id,
        "E2H_REPETITION": str(repetition),
    }
    for command in copy.success.commands:
        command.env = {**command.env, **injected}
    return copy


def _summarize(variant_id: str, runs: list[ExperimentRun]) -> VariantSummary:
    passed = sum(run.result.status is RunStatus.PASSED for run in runs)
    failed = sum(run.result.status is RunStatus.FAILED for run in runs)
    errors = sum(run.result.status is RunStatus.ERROR for run in runs)
    return VariantSummary(
        variant_id=variant_id,
        runs=len(runs),
        passed=passed,
        failed=failed,
        errors=errors,
        pass_rate=passed / len(runs),
        mean_duration_seconds=fmean(run.result.duration_seconds for run in runs),
    )


def run_experiment(
    spec: ExperimentSpec,
    capsule: TaskCapsule,
    workspace: Path,
    *,
    backend: ExecutionBackend = ExecutionBackend.AUTO,
    container_runtime: str | None = None,
) -> ExperimentExecution:
    """Execute every variant and repetition in deterministic declaration order."""
    started_at = datetime.now(UTC)
    started_clock = monotonic()
    runs: list[ExperimentRun] = []
    traces: list[Trace] = []

    for variant in spec.variants:
        for repetition in range(spec.repetitions):
            run_id = _matrix_run_id(spec.id, variant.id, repetition)
            result = run_capsule(
                _variant_capsule(capsule, variant, repetition),
                workspace,
                backend=backend,
                container_runtime=container_runtime,
            )
            trace = trace_from_run_result(
                result,
                run_id=run_id,
                experiment_id=spec.id,
                variant_id=variant.id,
                repetition=repetition,
                metadata={**spec.metadata, **variant.metadata},
            )
            runs.append(
                ExperimentRun(
                    run_id=run_id,
                    variant_id=variant.id,
                    repetition=repetition,
                    trace_id=trace.trace_id,
                    result=result,
                )
            )
            traces.append(trace)

    summaries = [
        _summarize(variant.id, [run for run in runs if run.variant_id == variant.id])
        for variant in spec.variants
    ]
    finished_at = datetime.now(UTC)
    experiment_result = ExperimentResult(
        experiment_id=spec.id,
        capsule_id=capsule.id,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=monotonic() - started_clock,
        runs=runs,
        summaries=summaries,
    )
    return ExperimentExecution(result=experiment_result, traces=traces)
