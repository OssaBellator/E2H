from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REUSABLE = ROOT / ".github" / "workflows" / "reusable-release-build.yml"
EXPECTED_ARTIFACT_INPUT = {
    "description": "Name for the verified release bundle artifact.",
    "required": True,
    "type": "string",
}
EXPECTED_TAG_INPUT = {
    "description": "Require vMAJOR.MINOR.PATCH to match pyproject and be reachable from main.",
    "required": False,
    "default": False,
    "type": "boolean",
}


def _workflow() -> tuple[dict[str, Any], str]:
    raw = REUSABLE.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed, raw


def _trigger(workflow: dict[str, Any]) -> Any:
    # PyYAML's YAML 1.1 resolver treats the plain key `on` as boolean true.
    return workflow.get("on", workflow.get(True))


def test_reusable_release_build_is_workflow_call_only_and_read_only() -> None:
    workflow, raw = _workflow()
    trigger = _trigger(workflow)
    assert trigger == {
        "workflow_call": {
            "inputs": {
                "artifact-name": EXPECTED_ARTIFACT_INPUT,
                "validate-release-tag": EXPECTED_TAG_INPUT,
            }
        }
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"build"}
    build = workflow["jobs"]["build"]
    assert build["permissions"] == {"contents": "read"}
    lowered = raw.lower()
    assert "id-token:" not in lowered
    assert "contents: write" not in lowered
    assert "attestations: write" not in lowered
    assert "artifact-metadata: write" not in lowered
    assert "secrets." not in lowered
    assert "environment:" not in lowered
    assert "pull_request_target" not in lowered
    assert "workflow_dispatch" not in lowered


def test_tag_validation_is_conditional_but_build_proof_is_unconditional() -> None:
    workflow, _ = _workflow()
    steps = workflow["jobs"]["build"]["steps"]
    validation = next(step for step in steps if step.get("name") == "Validate release tag and main ancestry")
    assert validation["if"] == "${{ inputs.validate-release-tag }}"

    required_steps = {
        "Prepare independent clean source copies",
        "Build release artifacts twice",
        "Export and canonicalize runtime SBOM twice",
        "Require byte-identical builds and canonical runtime SBOMs",
        "Seal and verify release artifacts",
        "Stage checksum-bound release bundle",
        "Upload verified release bundle",
    }
    selected = [step for step in steps if step.get("name") in required_steps]
    assert {step["name"] for step in selected} == required_steps
    assert all("if" not in step for step in selected)


def test_reusable_bundle_name_is_caller_controlled_but_contents_are_fixed() -> None:
    workflow, _ = _workflow()
    steps = workflow["jobs"]["build"]["steps"]
    upload = next(step for step in steps if step.get("name") == "Upload verified release bundle")
    assert upload["with"] == {
        "name": "${{ inputs.artifact-name }}",
        "path": "release/",
        "if-no-files-found": "error",
        "retention-days": 7,
    }
    stage = next(step for step in steps if step.get("name") == "Stage checksum-bound release bundle")
    run = stage["run"]
    assert "release/e2h-sbom.cdx.json" in run
    assert "release-manifest.json release-verification.json release-inspection.json" in run
    assert "release-checksums.txt" in run
