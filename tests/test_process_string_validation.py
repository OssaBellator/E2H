from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.models import TaskCapsule


def _capsule_data() -> dict[str, object]:
    return {
        "id": "process-strings",
        "goal": "Validate process launch strings.",
        "success": {"commands": [{"id": "check", "argv": ["python", "-V"]}]},
    }


def test_capsule_rejects_nul_in_argv() -> None:
    data = _capsule_data()
    data["success"] = {
        "commands": [{"id": "check", "argv": ["python", "bad\x00argument"]}]
    }

    with pytest.raises(ValidationError, match="contain no NUL"):
        TaskCapsule.model_validate(data)


@pytest.mark.parametrize("name", ["", "BAD=KEY", "BAD\x00KEY"])
def test_capsule_rejects_unsafe_environment_names(name: str) -> None:
    data = _capsule_data()
    data["success"] = {
        "commands": [{"id": "check", "argv": ["python"], "env": {name: "value"}}]
    }

    with pytest.raises(ValidationError, match="environment keys"):
        TaskCapsule.model_validate(data)


def test_capsule_rejects_nul_in_environment_value() -> None:
    data = _capsule_data()
    data["success"] = {
        "commands": [
            {"id": "check", "argv": ["python"], "env": {"SAFE": "bad\x00value"}}
        ]
    }

    with pytest.raises(ValidationError, match="environment values"):
        TaskCapsule.model_validate(data)


@pytest.mark.parametrize("field", ["initial_state", "cwd"])
def test_capsule_rejects_nul_in_filesystem_paths(field: str) -> None:
    data = _capsule_data()
    if field == "initial_state":
        data["initial_state"] = {"working_directory": "bad\x00path"}
    else:
        data["success"] = {
            "commands": [{"id": "check", "argv": ["python"], "cwd": "bad\x00path"}]
        }

    with pytest.raises(ValidationError, match="path must not contain NUL"):
        TaskCapsule.model_validate(data)


def test_capsule_rejects_nul_in_container_image() -> None:
    data = _capsule_data()
    data["sandbox"] = {"image": f"example\x00/image@sha256:{'0' * 64}"}

    with pytest.raises(ValidationError, match="container image must not contain NUL"):
        TaskCapsule.model_validate(data)
