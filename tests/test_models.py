from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.models import TaskCapsule


def capsule_data() -> dict[str, object]:
    return {
        "id": "smoke",
        "goal": "Pass a smoke test",
        "success": {"commands": [{"id": "ok", "argv": ["python", "-V"]}]},
    }


def test_capsule_accepts_minimal_document() -> None:
    capsule = TaskCapsule.model_validate(capsule_data())
    assert capsule.schema_version == "0.1"
    assert capsule.allowed_actions.network == "deny"


@pytest.mark.parametrize("path", ["/tmp", "../outside", "a/../../outside"])
def test_capsule_rejects_unsafe_paths(path: str) -> None:
    data = capsule_data()
    data["initial_state"] = {"working_directory": path}
    with pytest.raises(ValidationError):
        TaskCapsule.model_validate(data)


def test_capsule_requires_unique_check_ids() -> None:
    data = capsule_data()
    data["success"] = {
        "commands": [
            {"id": "duplicate", "argv": ["python", "-V"]},
            {"id": "duplicate", "argv": ["python", "-V"]},
        ]
    }
    with pytest.raises(ValidationError, match="unique"):
        TaskCapsule.model_validate(data)


def test_capsule_enforces_command_limit() -> None:
    data = capsule_data()
    data["limits"] = {"max_commands": 1}
    data["success"] = {
        "commands": [
            {"id": "one", "argv": ["python", "-V"]},
            {"id": "two", "argv": ["python", "-V"]},
        ]
    }
    with pytest.raises(ValidationError, match="max_commands"):
        TaskCapsule.model_validate(data)


def test_capsule_requires_command_permission() -> None:
    data = capsule_data()
    data["allowed_actions"] = {"tools": [], "network": "deny"}
    with pytest.raises(ValidationError, match=r"command.*permission"):
        TaskCapsule.model_validate(data)


def test_capsule_rejects_empty_argv_item() -> None:
    data = capsule_data()
    data["success"] = {"commands": [{"id": "bad", "argv": ["python", ""]}]}
    with pytest.raises(ValidationError, match="non-empty"):
        TaskCapsule.model_validate(data)
