from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "dependency-audit.yml"
PYPROJECT = ROOT / "pyproject.toml"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SETUP_UV_SHA = "c771a70e6277c0a99b617c7a806ffedaca235ff9"
SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"


def _workflow() -> tuple[dict[str, Any], str]:
    raw = WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed, raw


def _trigger(workflow: dict[str, Any]) -> Any:
    # PyYAML's YAML 1.1 resolver treats the plain key `on` as boolean true.
    return workflow.get("on", workflow.get(True))


def _pyproject() -> dict[str, Any]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def test_audit_tool_is_locked_outside_published_project_metadata() -> None:
    project = _pyproject()
    assert project["dependency-groups"]["audit"] == ["pip-audit==2.10.1"]
    assert "pip-audit==2.10.1" not in project["project"]["dependencies"]
    assert "pip-audit==2.10.1" not in project["project"]["optional-dependencies"]["dev"]


def test_dependency_audit_runs_on_main_changes_and_weekly() -> None:
    workflow, raw = _workflow()
    assert _trigger(workflow) == {
        "pull_request": {"branches": ["main"]},
        "push": {"branches": ["main"]},
        "schedule": [{"cron": "23 4 * * 1"}],
    }
    assert workflow["permissions"] == {"contents": "read"}
    lowered = raw.lower()
    assert "workflow_dispatch" not in lowered
    assert "pull_request_target" not in lowered
    assert "id-token:" not in lowered
    assert "contents: write" not in lowered
    assert "continue-on-error" not in lowered


def test_dependency_audit_uses_reviewed_bootstrap_actions() -> None:
    workflow, _ = _workflow()
    audit = workflow["jobs"]["audit"]
    assert audit["runs-on"] == "ubuntu-24.04"
    steps = audit["steps"]
    assert [step.get("uses") for step in steps if "uses" in step] == [
        f"actions/checkout@{CHECKOUT_SHA}",
        f"astral-sh/setup-uv@{SETUP_UV_SHA}",
        f"actions/setup-python@{SETUP_PYTHON_SHA}",
    ]


def test_dependency_audit_never_reresolves_the_target_graph() -> None:
    _, raw = _workflow()
    assert "uv sync --locked --no-dev --group audit" in raw
    assert "uv export \\" in raw
    assert "--format requirements.txt" in raw
    assert "--locked \\" in raw
    assert "--no-dev \\" in raw
    assert "--no-emit-local \\" in raw
    assert "pip-audit \\" in raw
    assert "--requirement .e2h/runtime-requirements.txt" in raw
    assert "--no-deps \\" in raw
    assert "--disable-pip \\" in raw
    assert "--require-hashes \\" in raw
    assert "--vulnerability-service pypi" in raw
    assert "--fix" not in raw
    assert "--ignore-vuln" not in raw
    assert "|| true" not in raw
