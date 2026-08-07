from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "codeql.yml"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
CODEQL_SHA = "6a90bf1f5426d0c367cad0b2000f3b721bab3122"


def _workflow() -> tuple[dict[str, Any], str]:
    raw = WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed, raw


def _trigger(workflow: dict[str, Any]) -> Any:
    # PyYAML's YAML 1.1 resolver treats the plain key `on` as boolean true.
    return workflow.get("on", workflow.get(True))


def test_codeql_scans_main_pull_requests_and_weekly() -> None:
    workflow, _ = _workflow()
    assert _trigger(workflow) == {
        "push": {"branches": ["main"]},
        "pull_request": {"branches": ["main"]},
        "schedule": [{"cron": "17 3 * * 4"}],
    }


def test_codeql_permissions_are_scan_only() -> None:
    workflow, raw = _workflow()
    assert workflow["permissions"] == {
        "actions": "read",
        "contents": "read",
        "security-events": "write",
    }
    lowered = raw.lower()
    assert "id-token:" not in lowered
    assert "contents: write" not in lowered
    assert "packages:" not in lowered
    assert "workflow_dispatch" not in lowered
    assert "pull_request_target" not in lowered


def test_codeql_scans_python_and_javascript_with_reviewed_pins() -> None:
    workflow, _ = _workflow()
    jobs = workflow["jobs"]
    assert set(jobs) == {"analyze"}
    analyze = jobs["analyze"]
    assert analyze["strategy"] == {
        "fail-fast": False,
        "matrix": {"language": ["python", "javascript-typescript"]},
    }
    steps = analyze["steps"]
    assert [step.get("uses") for step in steps] == [
        f"actions/checkout@{CHECKOUT_SHA}",
        f"github/codeql-action/init@{CODEQL_SHA}",
        f"github/codeql-action/analyze@{CODEQL_SHA}",
    ]
    assert steps[1]["with"] == {"languages": "${{ matrix.language }}"}
    assert steps[2]["with"] == {"category": "/language:${{ matrix.language }}"}
    assert not any("run" in step for step in steps)
