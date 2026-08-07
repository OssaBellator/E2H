from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REUSABLE = ROOT / ".github" / "workflows" / "reusable-release-build.yml"
CORE_CI = ROOT / ".github" / "workflows" / "ci.yml"
SETUP_PYTHON_PREFIX = "actions/setup-python@"
RELEASE_PYTHON = "3.13.14"


def _workflow(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _setup_python_version(job: dict[str, Any]) -> str:
    steps = job["steps"]
    assert isinstance(steps, list)
    matching = [
        step
        for step in steps
        if isinstance(step, dict)
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith(SETUP_PYTHON_PREFIX)
    ]
    assert len(matching) == 1
    inputs = matching[0]["with"]
    assert isinstance(inputs, dict)
    version = inputs["python-version"]
    assert isinstance(version, str)
    return version


def test_reusable_release_build_uses_exact_python_patch() -> None:
    build_job = _workflow(REUSABLE)["jobs"]["build"]
    assert _setup_python_version(build_job) == RELEASE_PYTHON


def test_release_python_pin_is_exact_not_a_minor_selector() -> None:
    parts = RELEASE_PYTHON.split(".")
    assert parts == ["3", "13", "14"]
    assert all(part.isdigit() for part in parts)


def test_compatibility_matrix_continues_tracking_supported_patch_updates() -> None:
    core = _workflow(CORE_CI)
    matrix = core["jobs"]["test"]["strategy"]["matrix"]
    assert matrix["python-version"] == ["3.11", "3.12", "3.13"]
