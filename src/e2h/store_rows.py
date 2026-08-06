"""Artifact validation and deterministic row normalization for the experiment store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import ValidationError

from e2h.experiment import ExperimentResult
from e2h.runner import RunResult
from e2h.store_models import ArtifactKind, MAX_ARTIFACT_BYTES

RunRow: TypeAlias = tuple[Any, ...]
CheckRow: TypeAlias = tuple[Any, ...]
SummaryRow: TypeAlias = tuple[Any, ...]


class ArtifactError(ValueError):
    """Raised when a replay artifact is invalid or internally ambiguous."""


def read_artifact(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ArtifactError(f"unable to stat artifact: {exc}") from exc
    if size > MAX_ARTIFACT_BYTES:
        raise ArtifactError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ArtifactError(f"unable to read artifact: {exc}") from exc
    if len(raw) != size:
        raise ArtifactError("artifact changed while being read")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"artifact is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ArtifactError("artifact root must be a JSON object")
    return raw, payload


def parse_artifact(
    payload: dict[str, Any],
    kind: ArtifactKind,
) -> tuple[Literal["run", "experiment"], RunResult | ExperimentResult]:
    selected = kind
    if selected is ArtifactKind.AUTO:
        selected = ArtifactKind.EXPERIMENT if "experiment_id" in payload else ArtifactKind.RUN
    try:
        if selected is ArtifactKind.EXPERIMENT:
            return "experiment", ExperimentResult.model_validate(payload)
        return "run", RunResult.model_validate(payload)
    except ValidationError as exc:
        raise ArtifactError(f"invalid {selected.value} artifact: {exc}") from exc


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _key(*parts: object) -> str:
    rendered = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def normalize_rows(
    source_sha256: str,
    kind: Literal["run", "experiment"],
    artifact: RunResult | ExperimentResult,
) -> tuple[list[RunRow], list[CheckRow], list[SummaryRow]]:
    """Normalize a validated artifact into deterministic relational rows."""
    if kind == "run":
        if not isinstance(artifact, RunResult):
            raise ArtifactError("run artifact type does not match selected kind")
        run_items = [(None, None, None, None, artifact)]
        summaries: list[SummaryRow] = []
    else:
        if not isinstance(artifact, ExperimentResult):
            raise ArtifactError("experiment artifact type does not match selected kind")
        run_items = [
            (
                artifact.experiment_id,
                item.run_id,
                item.variant_id,
                item.repetition,
                item.result,
            )
            for item in artifact.runs
        ]
        summaries = [
            (
                _key(source_sha256, artifact.experiment_id, summary.variant_id),
                source_sha256,
                artifact.experiment_id,
                artifact.capsule_id,
                summary.variant_id,
                summary.runs,
                summary.passed,
                summary.failed,
                summary.errors,
                summary.pass_rate,
                summary.mean_duration_seconds,
            )
            for summary in artifact.summaries
        ]

    runs: list[RunRow] = []
    checks: list[CheckRow] = []
    run_keys: set[str] = set()
    check_keys: set[str] = set()
    for experiment_id, run_id, variant_id, repetition, result in run_items:
        natural_id = run_id if run_id is not None else "standalone"
        run_key = _key(source_sha256, natural_id)
        if run_key in run_keys:
            raise ArtifactError(f"artifact contains duplicate run identity: {natural_id}")
        run_keys.add(run_key)
        runs.append(
            (
                run_key,
                source_sha256,
                experiment_id,
                run_id,
                variant_id,
                repetition,
                result.capsule_id,
                result.status.value,
                result.started_at,
                result.finished_at,
                result.duration_seconds,
                len(result.checks),
            )
        )
        for index, check in enumerate(result.checks):
            check_key = _key(run_key, index, check.id)
            if check_key in check_keys:
                raise ArtifactError(f"artifact contains duplicate check identity: {check.id}")
            check_keys.add(check_key)
            checks.append(
                (
                    check_key,
                    run_key,
                    source_sha256,
                    index,
                    check.id,
                    check.status.value,
                    check.exit_code,
                    check.duration_seconds,
                    check.cwd,
                    json.dumps(check.argv, separators=(",", ":"), ensure_ascii=False),
                    len(check.stdout),
                    len(check.stderr),
                    _digest_text(check.stdout),
                    _digest_text(check.stderr),
                    check.stdout_truncated,
                    check.stderr_truncated,
                    check.error,
                )
            )
    summary_keys = [str(row[0]) for row in summaries]
    if len(summary_keys) != len(set(summary_keys)):
        raise ArtifactError("artifact contains duplicate variant summaries")
    return runs, checks, summaries
