from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.experiment import ExperimentSpec, resolve_under_root
from e2h.variants import HarnessVariant


def _spec(**overrides: object) -> ExperimentSpec:
    payload: dict[str, object] = {
        "id": "nul-paths",
        "capsule": "capsule.json",
        "workspace": ".",
        "variants": [HarnessVariant(id="baseline")],
    }
    payload.update(overrides)
    return ExperimentSpec.model_validate(payload)


@pytest.mark.parametrize("field", ["capsule", "workspace"])
def test_experiment_spec_rejects_nul_paths(field: str) -> None:
    with pytest.raises(ValidationError, match="path must not contain NUL"):
        _spec(**{field: "bad\x00path"})


def test_resolve_under_root_rejects_nul_relative_before_filesystem_access(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="path must not contain NUL"):
        resolve_under_root(tmp_path, "bad\x00path")


def test_resolve_under_root_rejects_nul_root_before_filesystem_access() -> None:
    with pytest.raises(ValueError, match="experiment root must not contain NUL"):
        resolve_under_root(Path("bad\x00root"), "capsule.json")


def test_experiment_path_validation_preserves_valid_paths(tmp_path: Path) -> None:
    spec = _spec(capsule="capsules/task.json", workspace="workspace")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert spec.capsule == "capsules/task.json"
    assert resolve_under_root(tmp_path, spec.workspace) == workspace
