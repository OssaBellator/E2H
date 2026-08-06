"""Typed harness genomes and schema-aware capsule patch application."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.models import CommandCheck, TaskCapsule

_MAX_DOCUMENT_BYTES = 1_048_576
_MAX_METADATA_BYTES = 65_536
_PATCH_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class GenomeError(ValueError):
    """Raised when a harness genome cannot be loaded or applied safely."""


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


def _capsule_document(capsule: TaskCapsule) -> dict[str, Any]:
    document = capsule.model_dump(mode="json")
    for command in document["success"]["commands"]:
        command["expected_exit_codes"] = sorted(command["expected_exit_codes"])
    return document


def capsule_sha256(capsule: TaskCapsule) -> str:
    """Return a deterministic digest for a validated task capsule."""
    return hashlib.sha256(_canonical_json_bytes(_capsule_document(capsule))).hexdigest()


def _validate_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("path must be relative")
    if ".." in path.parts:
        raise ValueError("path must not contain parent traversal")
    return value


def _validate_environment_name(value: str) -> str:
    if not value or "=" in value or "\x00" in value:
        raise ValueError("environment name must be non-empty and contain neither '=' nor NUL")
    return value


class PatchBase(StrictModel):
    """Common identity for one atomic schema-aware patch."""

    id: str = Field(pattern=_PATCH_ID_PATTERN)

    def target_key(self) -> str:
        raise NotImplementedError


class GoalSetPatch(PatchBase):
    op: Literal["goal.set"] = "goal.set"
    value: str = Field(min_length=1, max_length=10_000)

    def target_key(self) -> str:
        return "goal"


class AllowedNetworkSetPatch(PatchBase):
    op: Literal["allowed_actions.network.set"] = "allowed_actions.network.set"
    value: Literal["deny", "allow"]

    def target_key(self) -> str:
        return "allowed_actions.network"


class CheckArgvSetPatch(PatchBase):
    op: Literal["check.argv.set"] = "check.argv.set"
    check_id: str = Field(pattern=_PATCH_ID_PATTERN)
    argv: list[str] = Field(min_length=1, max_length=256)

    @field_validator("argv")
    @classmethod
    def argv_items_must_be_non_empty(cls, value: list[str]) -> list[str]:
        if any(not item or "\x00" in item for item in value):
            raise ValueError("argv items must be non-empty and contain no NUL")
        return value

    def target_key(self) -> str:
        return f"check:{self.check_id}:argv"


class CheckCwdSetPatch(PatchBase):
    op: Literal["check.cwd.set"] = "check.cwd.set"
    check_id: str = Field(pattern=_PATCH_ID_PATTERN)
    value: str

    @field_validator("value")
    @classmethod
    def cwd_must_be_safe(cls, value: str) -> str:
        return _validate_relative_path(value)

    def target_key(self) -> str:
        return f"check:{self.check_id}:cwd"


class CheckEnvironmentSetPatch(PatchBase):
    op: Literal["check.env.set"] = "check.env.set"
    check_id: str = Field(pattern=_PATCH_ID_PATTERN)
    name: str = Field(max_length=1024)
    value: str = Field(max_length=65_536)

    @field_validator("name")
    @classmethod
    def name_must_be_process_safe(cls, value: str) -> str:
        return _validate_environment_name(value)

    @field_validator("value")
    @classmethod
    def value_must_be_process_safe(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("environment value must not contain NUL")
        return value

    def target_key(self) -> str:
        return f"check:{self.check_id}:env:{self.name}"


class CheckEnvironmentRemovePatch(PatchBase):
    op: Literal["check.env.remove"] = "check.env.remove"
    check_id: str = Field(pattern=_PATCH_ID_PATTERN)
    name: str = Field(max_length=1024)

    @field_validator("name")
    @classmethod
    def name_must_be_process_safe(cls, value: str) -> str:
        return _validate_environment_name(value)

    def target_key(self) -> str:
        return f"check:{self.check_id}:env:{self.name}"


class CheckTimeoutSetPatch(PatchBase):
    op: Literal["check.timeout.set"] = "check.timeout.set"
    check_id: str = Field(pattern=_PATCH_ID_PATTERN)
    seconds: float | None = None

    @field_validator("seconds")
    @classmethod
    def timeout_must_be_bounded(cls, value: float | None) -> float | None:
        if value is not None and not 0 < value <= 3600:
            raise ValueError("timeout seconds must be greater than zero and at most 3600")
        return value

    def target_key(self) -> str:
        return f"check:{self.check_id}:timeout_seconds"


class CheckExpectedExitCodesSetPatch(PatchBase):
    op: Literal["check.expected_exit_codes.set"] = "check.expected_exit_codes.set"
    check_id: str = Field(pattern=_PATCH_ID_PATTERN)
    values: list[int] = Field(min_length=1, max_length=256)

    @field_validator("values")
    @classmethod
    def values_must_be_unique_and_sorted(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("expected exit codes must be unique")
        return sorted(value)

    def target_key(self) -> str:
        return f"check:{self.check_id}:expected_exit_codes"


class CheckContinueOnFailureSetPatch(PatchBase):
    op: Literal["check.continue_on_failure.set"] = "check.continue_on_failure.set"
    check_id: str = Field(pattern=_PATCH_ID_PATTERN)
    value: bool

    def target_key(self) -> str:
        return f"check:{self.check_id}:continue_on_failure"


class DefaultTimeoutSetPatch(PatchBase):
    op: Literal["limits.default_timeout.set"] = "limits.default_timeout.set"
    seconds: float = Field(gt=0, le=3600)

    def target_key(self) -> str:
        return "limits.default_timeout_seconds"


class MaxOutputCharsSetPatch(PatchBase):
    op: Literal["limits.max_output_chars.set"] = "limits.max_output_chars.set"
    value: int = Field(ge=256, le=5_000_000)

    def target_key(self) -> str:
        return "limits.max_output_chars"


HarnessPatch = Annotated[
    GoalSetPatch
    | AllowedNetworkSetPatch
    | CheckArgvSetPatch
    | CheckCwdSetPatch
    | CheckEnvironmentSetPatch
    | CheckEnvironmentRemovePatch
    | CheckTimeoutSetPatch
    | CheckExpectedExitCodesSetPatch
    | CheckContinueOnFailureSetPatch
    | DefaultTimeoutSetPatch
    | MaxOutputCharsSetPatch,
    Field(discriminator="op"),
]


class HarnessGenome(StrictModel):
    """Ordered, immutable set of typed changes bound to one capsule digest."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_PATCH_ID_PATTERN)
    base_capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    patches: list[HarnessPatch] = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def patches_must_be_unambiguous(self) -> HarnessGenome:
        patch_ids = [patch.id for patch in self.patches]
        if len(patch_ids) != len(set(patch_ids)):
            raise ValueError("patch ids must be unique")
        targets = [patch.target_key() for patch in self.patches]
        if len(targets) != len(set(targets)):
            raise ValueError("a genome must not patch the same target more than once")
        if len(_canonical_json_bytes(self.metadata)) > _MAX_METADATA_BYTES:
            raise ValueError(f"genome metadata exceeds {_MAX_METADATA_BYTES} bytes")
        return self


class GenomeApplication(StrictModel):
    """Content-addressed result of applying one genome to one capsule."""

    schema_version: Literal["0.1"] = "0.1"
    genome_id: str = Field(pattern=_PATCH_ID_PATTERN)
    genome_sha256: str = Field(pattern=_SHA256_PATTERN)
    base_capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    result_capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    applied_patch_ids: list[str] = Field(min_length=1, max_length=100)
    capsule: TaskCapsule

    @model_validator(mode="after")
    def result_digest_must_match_capsule(self) -> GenomeApplication:
        if capsule_sha256(self.capsule) != self.result_capsule_sha256:
            raise ValueError("result capsule digest does not match embedded capsule")
        if len(self.applied_patch_ids) != len(set(self.applied_patch_ids)):
            raise ValueError("applied patch ids must be unique")
        return self


def genome_sha256(genome: HarnessGenome) -> str:
    """Return the canonical identity of a genome document."""
    return hashlib.sha256(_canonical_json_bytes(genome.model_dump(mode="json"))).hexdigest()


def _find_check(capsule: TaskCapsule, check_id: str) -> CommandCheck:
    for check in capsule.success.commands:
        if check.id == check_id:
            return check
    raise GenomeError(f"unknown check id: {check_id}")


def _reject_noop(patch: PatchBase, before: Any, after: Any) -> None:
    if before == after:
        raise GenomeError(f"patch {patch.id} is a no-op")


def _apply_patch(capsule: TaskCapsule, patch: HarnessPatch) -> None:
    if isinstance(patch, GoalSetPatch):
        before = capsule.goal
        capsule.goal = patch.value
        _reject_noop(patch, before, capsule.goal)
        return
    if isinstance(patch, AllowedNetworkSetPatch):
        before = capsule.allowed_actions.network
        capsule.allowed_actions.network = patch.value
        _reject_noop(patch, before, capsule.allowed_actions.network)
        return
    if isinstance(patch, CheckArgvSetPatch):
        check = _find_check(capsule, patch.check_id)
        before = list(check.argv)
        check.argv = list(patch.argv)
        _reject_noop(patch, before, check.argv)
        return
    if isinstance(patch, CheckCwdSetPatch):
        check = _find_check(capsule, patch.check_id)
        before = check.cwd
        check.cwd = patch.value
        _reject_noop(patch, before, check.cwd)
        return
    if isinstance(patch, CheckEnvironmentSetPatch):
        check = _find_check(capsule, patch.check_id)
        before = check.env.get(patch.name)
        check.env[patch.name] = patch.value
        _reject_noop(patch, before, check.env[patch.name])
        return
    if isinstance(patch, CheckEnvironmentRemovePatch):
        check = _find_check(capsule, patch.check_id)
        if patch.name not in check.env:
            raise GenomeError(f"patch {patch.id} cannot remove missing environment variable")
        del check.env[patch.name]
        return
    if isinstance(patch, CheckTimeoutSetPatch):
        check = _find_check(capsule, patch.check_id)
        before = check.timeout_seconds
        check.timeout_seconds = patch.seconds
        _reject_noop(patch, before, check.timeout_seconds)
        return
    if isinstance(patch, CheckExpectedExitCodesSetPatch):
        check = _find_check(capsule, patch.check_id)
        before = set(check.expected_exit_codes)
        check.expected_exit_codes = set(patch.values)
        _reject_noop(patch, before, check.expected_exit_codes)
        return
    if isinstance(patch, CheckContinueOnFailureSetPatch):
        check = _find_check(capsule, patch.check_id)
        before = check.continue_on_failure
        check.continue_on_failure = patch.value
        _reject_noop(patch, before, check.continue_on_failure)
        return
    if isinstance(patch, DefaultTimeoutSetPatch):
        before = capsule.limits.default_timeout_seconds
        capsule.limits.default_timeout_seconds = patch.seconds
        _reject_noop(patch, before, capsule.limits.default_timeout_seconds)
        return
    if isinstance(patch, MaxOutputCharsSetPatch):
        before = capsule.limits.max_output_chars
        capsule.limits.max_output_chars = patch.value
        _reject_noop(patch, before, capsule.limits.max_output_chars)
        return
    raise GenomeError(f"unsupported patch type: {type(patch).__name__}")


def apply_genome(genome: HarnessGenome, capsule: TaskCapsule) -> GenomeApplication:
    """Apply a genome without executing commands or mutating the input capsule."""
    base_digest = capsule_sha256(capsule)
    if genome.base_capsule_sha256 != base_digest:
        raise GenomeError("genome base capsule digest does not match the supplied capsule")
    updated = capsule.model_copy(deep=True)
    for patch in genome.patches:
        _apply_patch(updated, patch)
    try:
        validated = TaskCapsule.model_validate(updated.model_dump())
    except ValueError as exc:
        raise GenomeError(f"patched capsule violates the task capsule schema: {exc}") from exc
    result_digest = capsule_sha256(validated)
    return GenomeApplication(
        genome_id=genome.id,
        genome_sha256=genome_sha256(genome),
        base_capsule_sha256=base_digest,
        result_capsule_sha256=result_digest,
        applied_patch_ids=[patch.id for patch in genome.patches],
        capsule=validated,
    )


def materialize_application(application: GenomeApplication) -> TaskCapsule:
    """Return a detached capsule after re-validating the application digest."""
    validated = GenomeApplication.model_validate(application.model_dump())
    return validated.capsule.model_copy(deep=True)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _read_document(path: Path, *, noun: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GenomeError(f"unable to read {noun}: {exc}") from exc
    if len(raw) > _MAX_DOCUMENT_BYTES:
        raise GenomeError(f"{noun} exceeds {_MAX_DOCUMENT_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GenomeError(f"{noun} must be UTF-8") from exc
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text, parse_constant=_reject_json_constant)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(text)
        else:
            raise GenomeError(f"{noun} must use .json, .yaml, or .yml")
    except (json.JSONDecodeError, yaml.YAMLError, ValueError) as exc:
        raise GenomeError(f"invalid {noun} syntax: {exc}") from exc
    if not isinstance(data, dict):
        raise GenomeError(f"{noun} root must be an object")
    return data


def load_genome(path: Path) -> HarnessGenome:
    """Load and validate a genome document."""
    try:
        return HarnessGenome.model_validate(_read_document(path, noun="genome"))
    except ValueError as exc:
        if isinstance(exc, GenomeError):
            raise
        raise GenomeError(f"invalid genome: {exc}") from exc


def load_genome_application(path: Path) -> GenomeApplication:
    """Load and verify a prior genome application document."""
    try:
        return GenomeApplication.model_validate(_read_document(path, noun="genome application"))
    except ValueError as exc:
        if isinstance(exc, GenomeError):
            raise
        raise GenomeError(f"invalid genome application: {exc}") from exc
