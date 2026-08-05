"""Load E2H documents from JSON or YAML."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from e2h.experiment import ExperimentSpec
from e2h.models import TaskCapsule


class CapsuleLoadError(ValueError):
    """Raised when a capsule cannot be decoded or validated."""


class ExperimentLoadError(ValueError):
    """Raised when an experiment cannot be decoded or validated."""


def _load_mapping(path: Path, *, noun: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"unable to read {noun}: {exc}") from exc

    try:
        data: Any
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(text)
        else:
            raise ValueError(f"{noun} must use .json, .yaml, or .yml")
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"invalid {noun} syntax: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{noun} root must be an object")
    return data


def load_capsule(path: Path) -> TaskCapsule:
    """Load and validate a capsule from a supported file."""
    try:
        data = _load_mapping(path, noun="capsule")
        return TaskCapsule.model_validate(data)
    except ValueError as exc:
        raise CapsuleLoadError(str(exc)) from exc


def load_experiment(path: Path) -> ExperimentSpec:
    """Load and validate an experiment specification."""
    try:
        data = _load_mapping(path, noun="experiment")
        return ExperimentSpec.model_validate(data)
    except ValueError as exc:
        raise ExperimentLoadError(str(exc)) from exc
