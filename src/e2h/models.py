"""Typed schemas for reproducible E2H task capsules."""

from __future__ import annotations

import math
import re
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_MAX_CONTAINER_NUMERIC_ID = (1 << 31) - 1
_MAX_METADATA_STRUCTURE_DEPTH = 128


def _validate_relative_path(value: str) -> str:
    """Reject unsafe relative paths while preserving POSIX notation."""
    if "\x00" in value:
        raise ValueError("path must not contain NUL")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("path must be relative")
    if ".." in path.parts:
        raise ValueError("path must not contain parent traversal")
    return value


def _validate_json_compatible(
    value: Any,
    *,
    path: str = "$",
    active: set[int] | None = None,
    depth: int = 0,
) -> None:
    if depth > _MAX_METADATA_STRUCTURE_DEPTH:
        raise ValueError(
            "metadata structure exceeds maximum nesting depth "
            f"({_MAX_METADATA_STRUCTURE_DEPTH})"
        )
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite number")
        return
    if active is None:
        active = set()
    if type(value) is list:
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} contains a recursive value")
        active.add(identity)
        try:
            for index, item in enumerate(value):
                _validate_json_compatible(
                    item,
                    path=f"{path}[{index}]",
                    active=active,
                    depth=depth + 1,
                )
        finally:
            active.remove(identity)
        return
    if type(value) is dict:
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} contains a recursive value")
        active.add(identity)
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError(f"{path} mapping keys must be strings")
                _validate_json_compatible(
                    item,
                    path=f"{path}[{key!r}]",
                    active=active,
                    depth=depth + 1,
                )
        finally:
            active.remove(identity)
        return
    raise ValueError(f"{path} contains unsupported value type {type(value).__name__}")


def _validate_metadata(value: Any, *, noun: str) -> Any:
    try:
        _validate_json_compatible(value)
    except (RecursionError, ValueError) as exc:
        raise ValueError(f"{noun} metadata must contain canonical JSON data: {exc}") from exc
    return value


class StrictModel(BaseModel):
    """Base class that rejects unknown fields to keep capsules deterministic."""

    model_config = ConfigDict(extra="forbid", revalidate_instances="always")


class InitialState(StrictModel):
    """Workspace state required before checks are executed."""

    working_directory: str = "."

    @field_validator("working_directory")
    @classmethod
    def safe_working_directory(cls, value: str) -> str:
        return _validate_relative_path(value)


class AllowedActions(StrictModel):
    """Declared execution permissions for the task."""

    tools: list[str] = Field(default_factory=lambda: ["command"])
    network: Literal["deny", "allow"] = "deny"


class ExecutionLimits(StrictModel):
    """Bounds that protect replay workers from runaway tasks."""

    max_commands: int = Field(default=50, ge=1, le=1000)
    default_timeout_seconds: float = Field(default=30.0, gt=0, le=3600)
    max_output_chars: int = Field(default=20_000, ge=256, le=5_000_000)


class ContainerSandbox(StrictModel):
    """Immutable container image and bounded runtime policy for capsule checks."""

    engine: Literal["docker"] = "docker"
    image: str = Field(min_length=1, max_length=500)
    workspace_access: Literal["read_only", "read_write"] = "read_only"
    user: str = Field(default="65532:65532", max_length=64)
    read_only_root: bool = True
    pull_policy: Literal["never", "missing"] = "never"
    pids_limit: int = Field(default=256, ge=16, le=4096)
    memory_mb: int = Field(default=1024, ge=64, le=1_048_576)
    cpus: float = Field(default=1.0, ge=0.1, le=128)
    tmpfs_mb: int = Field(default=64, ge=16, le=4096)

    @field_validator("image")
    @classmethod
    def image_must_be_immutable(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("container image must not contain NUL")
        if value.startswith("-"):
            raise ValueError("container image must not begin with '-' or resemble a Docker option")
        if re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("container image must use an immutable digest reference")
        return value

    @field_validator("user")
    @classmethod
    def user_must_be_non_root(cls, value: str) -> str:
        parts = value.split(":")
        if (
            len(parts) not in {1, 2}
            or any(not part or not part.isascii() or not part.isdecimal() for part in parts)
        ):
            raise ValueError("container user must be a numeric uid or uid:gid")
        ids = [int(part) for part in parts]
        if any(identifier > _MAX_CONTAINER_NUMERIC_ID for identifier in ids):
            raise ValueError(
                f"container user ids must not exceed {_MAX_CONTAINER_NUMERIC_ID}"
            )
        if ids[0] == 0:
            raise ValueError("container user must be non-root")
        return value


class CommandCheck(StrictModel):
    """One deterministic command-based success check."""

    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
    argv: list[str] = Field(min_length=1)
    description: str | None = None
    cwd: str = "."
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    expected_exit_codes: set[int] = Field(default_factory=lambda: {0}, min_length=1)
    continue_on_failure: bool = False

    @field_validator("cwd")
    @classmethod
    def safe_cwd(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("argv")
    @classmethod
    def argv_items_must_be_process_safe(cls, value: list[str]) -> list[str]:
        if any(not item or "\x00" in item for item in value):
            raise ValueError("argv items must be non-empty and contain no NUL")
        return value

    @field_validator("env")
    @classmethod
    def environment_must_be_process_safe(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not key or "=" in key or "\x00" in key:
                raise ValueError(
                    "environment keys must be non-empty and contain neither '=' nor NUL"
                )
            if "\x00" in item:
                raise ValueError("environment values must not contain NUL")
        return value

    @field_validator("expected_exit_codes")
    @classmethod
    def expected_exit_codes_must_be_non_negative(cls, value: set[int]) -> set[int]:
        if any(code < 0 for code in value):
            raise ValueError("expected exit codes must be non-negative")
        return value


class SuccessSpec(StrictModel):
    """Executable success criteria."""

    commands: list[CommandCheck] = Field(min_length=1)

    @model_validator(mode="after")
    def command_ids_must_be_unique(self) -> SuccessSpec:
        ids = [command.id for command in self.commands]
        if len(ids) != len(set(ids)):
            raise ValueError("command check ids must be unique")
        return self


class TaskCapsule(StrictModel):
    """Immutable, portable description of an executable task evaluation."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
    goal: str = Field(min_length=1, max_length=10_000)
    initial_state: InitialState = Field(default_factory=InitialState)
    allowed_actions: AllowedActions = Field(default_factory=AllowedActions)
    limits: ExecutionLimits = Field(default_factory=ExecutionLimits)
    sandbox: ContainerSandbox | None = None
    success: SuccessSpec
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def metadata_must_be_canonical_json(cls, value: Any) -> Any:
        return _validate_metadata(value, noun="task capsule")

    @model_validator(mode="after")
    def command_count_must_fit_limit(self) -> TaskCapsule:
        if len(self.success.commands) > self.limits.max_commands:
            raise ValueError("success.commands exceeds limits.max_commands")
        if "command" not in self.allowed_actions.tools:
            raise ValueError("command checks require the 'command' tool permission")
        return self
