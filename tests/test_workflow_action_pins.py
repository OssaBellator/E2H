from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

REVIEWED_ACTION_PINS = {
    "actions/attest": "508db95dd578ae2727ebd6217d5ba78e4fbda05d",
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/setup-node": "e51e5fe84fc33b4c73ebe40526b2694712b5b858",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "astral-sh/setup-uv": "c771a70e6277c0a99b617c7a806ffedaca235ff9",
    "github/codeql-action": "6a90bf1f5426d0c367cad0b2000f3b721bab3122",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}


def _workflow_paths() -> list[Path]:
    return sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])


def _load_workflow(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), path
    return parsed


def _external_uses(workflow: dict[str, Any]) -> list[str]:
    uses: list[str] = []
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict)
    for job in jobs.values():
        assert isinstance(job, dict)
        steps = job.get("steps", [])
        assert isinstance(steps, list)
        for step in steps:
            assert isinstance(step, dict)
            value = step.get("uses")
            if value is None:
                continue
            assert isinstance(value, str)
            if value.startswith("./"):
                continue
            uses.append(value)
    return uses


def _action_repository(action: str) -> str:
    parts = action.split("/")
    assert len(parts) >= 2, f"external action must use owner/repository syntax: {action}"
    return "/".join(parts[:2])


def test_every_external_workflow_action_uses_reviewed_immutable_commit() -> None:
    paths = _workflow_paths()
    assert paths
    seen_actions: set[str] = set()

    for path in paths:
        workflow = _load_workflow(path)
        for value in _external_uses(workflow):
            action, separator, revision = value.partition("@")
            assert separator == "@", f"workflow action has no revision in {path}: {value}"
            repository = _action_repository(action)
            assert repository in REVIEWED_ACTION_PINS, (
                f"unreviewed workflow action in {path}: {repository}"
            )
            expected = REVIEWED_ACTION_PINS[repository]
            assert revision == expected, (
                f"workflow action is not pinned to the reviewed commit in {path}: "
                f"{action}@{revision} != {repository}@{expected}"
            )
            assert len(revision) == 40
            assert all(character in "0123456789abcdef" for character in revision)
            seen_actions.add(repository)

    assert seen_actions == set(REVIEWED_ACTION_PINS)


def test_workflows_do_not_use_mutable_action_refs_in_raw_yaml() -> None:
    for path in _workflow_paths():
        text = path.read_text(encoding="utf-8")
        assert "uses: actions/checkout@v" not in text
        assert "uses: actions/setup-python@v" not in text
        assert "uses: actions/setup-node@v" not in text
        assert "uses: actions/upload-artifact@v" not in text
        assert "uses: actions/download-artifact@v" not in text
        assert "uses: actions/attest@v" not in text
        assert "uses: astral-sh/setup-uv@v" not in text
        assert "uses: github/codeql-action/init@v" not in text
        assert "uses: github/codeql-action/analyze@v" not in text
        assert "uses: pypa/gh-action-pypi-publish@v" not in text
