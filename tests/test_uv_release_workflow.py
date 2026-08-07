from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
BUILD_CONSTRAINTS = ROOT / "build-constraints.txt"
WORKFLOWS = [
    ROOT / ".github" / "workflows" / "release-integrity.yml",
    ROOT / ".github" / "workflows" / "publish-pypi.yml",
]
UV_VERSION = "0.12.2"
HATCHLING_VERSION = "1.31.0"


def _load(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    for job in jobs.values():
        assert isinstance(job, dict)
        value = job.get("steps", [])
        assert isinstance(value, list)
        steps.extend(step for step in value if isinstance(step, dict))
    return steps


def test_project_pins_uv_frontend_version() -> None:
    with PYPROJECT.open("rb") as handle:
        project = tomllib.load(handle)
    assert project["tool"]["uv"]["required-version"] == f"=={UV_VERSION}"


def test_release_build_backend_has_separate_exact_constraint() -> None:
    assert BUILD_CONSTRAINTS.read_text(encoding="utf-8") == f"hatchling=={HATCHLING_VERSION}\n"


def test_release_builds_pin_uv_frontend_version() -> None:
    for path in WORKFLOWS:
        setup_steps = [
            step
            for step in _steps(_load(path))
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
        ]
        assert len(setup_steps) == 1, path
        assert setup_steps[0].get("with", {}).get("version") == UV_VERSION, path


def test_release_builds_require_committed_uv_lock() -> None:
    for path in WORKFLOWS:
        runs = [str(step.get("run", "")) for step in _steps(_load(path))]
        syncs = [run for run in runs if run.strip().startswith("uv sync")]
        assert syncs == ["uv sync --locked --extra dev"], path
        assert "uv sync --extra dev" not in path.read_text(encoding="utf-8")


def test_release_builds_apply_exact_backend_constraint() -> None:
    expected = "uv build --build-constraint build-constraints.txt"
    for path in WORKFLOWS:
        runs = "\n".join(str(step.get("run", "")) for step in _steps(_load(path)))
        assert runs.count(expected) == 2, path
