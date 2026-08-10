"""Load E2H documents from JSON or YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from e2h.document import load_mapping_document
from e2h.experiment import ExperimentSpec
from e2h.models import TaskCapsule


class CapsuleLoadError(ValueError):
    """Raised when a capsule cannot be decoded or validated."""


class ExperimentLoadError(ValueError):
    """Raised when an experiment cannot be decoded or validated."""


def _load_mapping(
    path: Path,
    *,
    noun: str,
    containment_root: Path | None = None,
) -> dict[str, Any]:
    return load_mapping_document(path, noun=noun, containment_root=containment_root)


def load_capsule(
    path: Path,
    *,
    containment_root: Path | None = None,
) -> TaskCapsule:
    """Load and validate a capsule from a supported file."""
    try:
        data = _load_mapping(path, noun="capsule", containment_root=containment_root)
        return TaskCapsule.model_validate(data)
    except ValueError as exc:
        raise CapsuleLoadError(str(exc)) from exc


def load_experiment(
    path: Path,
    *,
    containment_root: Path | None = None,
) -> ExperimentSpec:
    """Load and validate an experiment specification."""
    try:
        data = _load_mapping(path, noun="experiment", containment_root=containment_root)
        return ExperimentSpec.model_validate(data)
    except ValueError as exc:
        raise ExperimentLoadError(str(exc)) from exc
