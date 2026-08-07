from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "dependency-review.yml"
ACTION_SHA = "1e966ba708e4b64bb0c9bfb7abb5a232840be16a"


def _workflow() -> tuple[dict[str, Any], str]:
    raw = WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed, raw


def _trigger(workflow: dict[str, Any]) -> Any:
    return workflow.get("on", workflow.get(True))


def test_dependency_review_is_pull_request_only_and_read_only() -> None:
    workflow, raw = _workflow()
    assert _trigger(workflow) == {"pull_request": {"branches": ["main"]}}
    assert workflow["permissions"] == {"contents": "read"}
    lowered = raw.lower()
    assert "id-token:" not in lowered
    assert "pull-requests: write" not in lowered
    assert "workflow_dispatch" not in lowered
    assert "pull_request_target" not in lowered


def test_dependency_review_blocks_moderate_or_worse_across_all_scopes() -> None:
    workflow, _ = _workflow()
    jobs = workflow["jobs"]
    assert set(jobs) == {"dependency-review"}
    steps = jobs["dependency-review"]["steps"]
    assert len(steps) == 1
    step = steps[0]
    assert step["uses"] == f"actions/dependency-review-action@{ACTION_SHA}"
    assert step["with"] == {
        "fail-on-severity": "moderate",
        "fail-on-scopes": "runtime, development, unknown",
        "show-patched-versions": True,
    }
    assert "run" not in step
