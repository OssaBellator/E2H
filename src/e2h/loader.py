"""Load task capsules from JSON or YAML."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from e2h.models import TaskCapsule


class CapsuleLoadError(ValueError):
    """Raised when a capsule cannot be decoded or validated."""


def load_capsule(path: Path) -> TaskCapsule:
    """Load and validate a capsule from a supported file."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapsuleLoadError(f"unable to read capsule: {exc}") from exc

    try:
        data: Any
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            data = yaml.safe_load(text)
        else:
            raise CapsuleLoadError("capsule must use .json, .yaml, or .yml")
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise CapsuleLoadError(f"invalid capsule syntax: {exc}") from exc

    if not isinstance(data, dict):
        raise CapsuleLoadError("capsule root must be an object")

    try:
        return TaskCapsule.model_validate(data)
    except ValidationError as exc:
        raise CapsuleLoadError(str(exc)) from exc
