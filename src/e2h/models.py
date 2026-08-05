"""Typed schemas for reproducible E2H task capsules."""

from __future__ import annotations

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
    success: SuccessSpec
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def command_count_must_fit_limit(self) -> TaskCapsule:
        if len(self.success.commands) > self.limits.max_commands:
            raise ValueError("success.commands exceeds limits.max_commands")
        if "command" not in self.allowed_actions.tools:
            raise ValueError("command checks require the 'command' tool permission")
        return self
