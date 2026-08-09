"""Artifact validation and deterministic row normalization for the experiment store."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import ValidationError

from e2h.experiment import ExperimentResult
from e2h.runner import RunResult
from e2h.store_models import MAX_ARTIFACT_BYTES, ArtifactKind

RunItem: TypeAlias = tuple[str | None, str | None, str | None, int | None, RunResult]
RunRow: TypeAlias = tuple[Any, ...]
CheckRow: TypeAlias = tuple[Any, ...]
SummaryRow: TypeAlias = tuple[Any, ...]
FailureRow: TypeAlias = tuple[Any, ...]

_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_ARTIFACT_DIR_FD_SUPPORTED = _OPEN_SUPPORTS_DIR_FD and _STAT_SUPPORTS_DIR_FD


class ArtifactError(ValueError):
    """Raised when a replay artifact is invalid or internally ambiguous."""


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key!r}")
        result[key] = value
    return result


def _requested_parent_identity(requested_parent: Path) -> os.stat_result:
    try:
        current_parent = requested_parent.resolve(strict=True)
        return current_parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise ArtifactError(f"unable to restat artifact parent: {exc}") from exc


def _read_artifact_bytes(path: Path) -> bytes:
    requested_parent = path.parent.absolute()
    try:
        parent = requested_parent.resolve(strict=True)
        parent_expected = parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise ArtifactError(f"unable to inspect artifact parent: {exc}") from exc
    if stat.S_ISLNK(parent_expected.st_mode) or not stat.S_ISDIR(parent_expected.st_mode):
        raise ArtifactError("artifact parent must be a real directory")

    parent_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        parent_descriptor = os.open(parent, parent_flags)
    except OSError as exc:
        raise ArtifactError(f"unable to open artifact parent: {exc}") from exc

    descriptor: int | None = None
    try:
        parent_opened = os.fstat(parent_descriptor)
        requested_opened = _requested_parent_identity(requested_parent)
        if (
            not stat.S_ISDIR(parent_opened.st_mode)
            or _stat_identity(parent_opened) != _stat_identity(parent_expected)
            or _stat_identity(requested_opened) != _stat_identity(parent_opened)
        ):
            raise ArtifactError("artifact parent changed while opening")

        try:
            expected = (
                os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if _ARTIFACT_DIR_FD_SUPPORTED
                else (parent / path.name).stat(follow_symlinks=False)
            )
        except OSError as exc:
            raise ArtifactError(f"unable to stat artifact: {exc}") from exc
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
            raise ArtifactError("artifact must be a regular file")
        if expected.st_size > MAX_ARTIFACT_BYTES:
            raise ArtifactError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = (
                os.open(path.name, flags, dir_fd=parent_descriptor)
                if _ARTIFACT_DIR_FD_SUPPORTED
                else os.open(parent / path.name, flags)
            )
        except OSError as exc:
            raise ArtifactError(f"unable to open artifact: {exc}") from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _stat_identity(opened) != _stat_identity(expected):
            raise ArtifactError("artifact changed while opening")
        if opened.st_size > MAX_ARTIFACT_BYTES:
            raise ArtifactError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes")

        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            raw = handle.read(MAX_ARTIFACT_BYTES + 1)
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ArtifactError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes")

        after = os.fstat(descriptor)
        try:
            current = (
                os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if _ARTIFACT_DIR_FD_SUPPORTED
                else (parent / path.name).stat(follow_symlinks=False)
            )
            parent_after = os.fstat(parent_descriptor)
            parent_current = _requested_parent_identity(requested_parent)
        except OSError as exc:
            raise ArtifactError(f"unable to restat artifact after reading: {exc}") from exc
        if (
            _stat_identity(after) != _stat_identity(opened)
            or _stat_identity(current) != _stat_identity(opened)
            or len(raw) != opened.st_size
        ):
            raise ArtifactError("artifact changed while being read")
        if _stat_identity(parent_after) != _stat_identity(parent_opened) or _stat_identity(
            parent_current
        ) != _stat_identity(parent_opened):
            raise ArtifactError("artifact parent changed while being read")
        return raw
    except OSError as exc:
        raise ArtifactError(f"unable to read artifact: {exc}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.close(parent_descriptor)


def read_artifact(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = _read_artifact_bytes(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArtifactError(f"artifact is not valid UTF-8 JSON: {exc}") from exc
    try:
        payload = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (json.JSONDecodeError, ValueError) as exc:
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
) -> tuple[list[RunRow], list[CheckRow], list[SummaryRow], list[FailureRow]]:
    """Normalize a validated artifact into deterministic relational rows."""
    run_items: list[RunItem]
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
    failures: list[FailureRow] = []
    run_keys: set[str] = set()
    check_keys: set[str] = set()
    failure_keys: set[str] = set()
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
            if check.failure is not None:
                failure_key = _key(check_key, check.failure.code.value)
                if failure_key in failure_keys:
                    raise ArtifactError(f"artifact contains duplicate failure identity: {check.id}")
                failure_keys.add(failure_key)
                failures.append(
                    (
                        failure_key,
                        check_key,
                        run_key,
                        source_sha256,
                        check.failure.category.value,
                        check.failure.code.value,
                        check.failure.impact.value,
                        check.failure.retryability.value,
                        check.failure.summary,
                        json.dumps(
                            check.failure.details,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                        check.failure.caused_by_check_id,
                        json.dumps(
                            [cause.value for cause in check.failure.causes],
                            separators=(",", ":"),
                        ),
                    )
                )
    summary_keys = [str(row[0]) for row in summaries]
    if len(summary_keys) != len(set(summary_keys)):
        raise ArtifactError("artifact contains duplicate variant summaries")
    return runs, checks, summaries, failures
