from __future__ import annotations

import pytest
from pydantic import ValidationError

import e2h.models as models
from e2h.models import TaskCapsule


def _nested_metadata(depth: int) -> dict[str, object]:
    value: object = 0
    for _ in range(depth):
        value = [value]
    return {"value": value}


def _capsule(metadata: dict[str, object]) -> TaskCapsule:
    return TaskCapsule(
        id="metadata-depth",
        goal="exercise metadata depth validation",
        success={"commands": [{"id": "check", "argv": ["true"]}]},
        metadata=metadata,
    )


def test_task_capsule_metadata_accepts_maximum_supported_structure_depth() -> None:
    capsule = _capsule(_nested_metadata(models._MAX_METADATA_STRUCTURE_DEPTH - 1))

    assert "value" in capsule.metadata


def test_task_capsule_metadata_rejects_structure_beyond_depth_limit() -> None:
    with pytest.raises(ValidationError, match="maximum nesting depth"):
        _capsule(_nested_metadata(models._MAX_METADATA_STRUCTURE_DEPTH))


def test_task_capsule_metadata_converts_parser_independent_recursion_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def recurse(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RecursionError("synthetic metadata recursion")

    monkeypatch.setattr(models, "_validate_json_compatible", recurse)

    with pytest.raises(ValidationError, match="canonical JSON data"):
        _capsule({"value": 0})
