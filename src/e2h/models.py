"""Typed schemas for reproducible E2H task capsules."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _validate_relative_path(value: str) -> str:
    """Reject absolute and parent-traversing paths while preserving POSIX notation."""
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("path must be relative")
    if ".." in path.parts:
        raise ValueError("path must not contain parent traversal")
    return value


class StrictModel(BaseModel):
    """Base class that rejects unknown fields to keep capsules deterministic."""

    model_config = ConfigDict(extra="forbid")


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
        if re.fullmatch(r"[^@\s]+@sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("container image must use an immutable digest reference")
        return value

    @field_validator("user")
    @classmethod
    def user_must_be_non_root(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) not in {1, 2} or any(not part.isdigit() for part in parts):
            raise ValueError("container user must be a numeric uid or uid:gid")
        if int(parts[0]) == 0:
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
    def argv_items_must_be_non_empty(cls, value: list[str]) -> list[str]:
        if any(not item for item in value):
            raise ValueError("argv items must be non-empty strings")
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

    @model_validator(mode="after")
    def command_count_must_fit_limit(self) -> TaskCapsule:
        if len(self.success.commands) > self.limits.max_commands:
            raise ValueError("success.commands exceeds limits.max_commands")
        if "command" not in self.allowed_actions.tools:
            raise ValueError("command checks require the 'command' tool permission")
        return self
