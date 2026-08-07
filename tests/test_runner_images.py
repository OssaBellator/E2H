from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
MOVING_RUNNER_ALIASES = {"ubuntu-latest", "windows-latest", "macos-latest"}


def _workflow_paths() -> list[Path]:
    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


def test_workflows_do_not_use_moving_runner_aliases() -> None:
    paths = _workflow_paths()
    assert paths
    explicit_ubuntu_jobs = 0

    for path in paths:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict), path
        jobs = parsed.get("jobs")
        assert isinstance(jobs, dict), path
        for job_name, job in jobs.items():
            assert isinstance(job, dict), (path, job_name)
            runs_on = job.get("runs-on")
            assert runs_on is not None, (path, job_name)
            if not isinstance(runs_on, str):
                continue
            assert runs_on not in MOVING_RUNNER_ALIASES, (
                f"moving runner alias in {path}:{job_name}: {runs_on}"
            )
            if runs_on == "ubuntu-24.04":
                explicit_ubuntu_jobs += 1

    assert explicit_ubuntu_jobs >= 1


def test_raw_workflow_yaml_has_no_latest_runner_labels() -> None:
    for path in _workflow_paths():
        raw = path.read_text(encoding="utf-8")
        assert "runs-on: ubuntu-latest" not in raw
        assert "runs-on: windows-latest" not in raw
        assert "runs-on: macos-latest" not in raw
