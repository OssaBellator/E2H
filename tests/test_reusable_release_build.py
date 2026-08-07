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
    validation = next(
        step for step in steps if step.get("name") == "Validate release tag and main ancestry"
    )
    assert validation["if"] == "${{ inputs.validate-release-tag }}"

    required_steps = {
        "Prepare independent clean source copies",
        "Build release artifacts twice",
        "Export and canonicalize runtime SBOM twice",
        "Require byte-identical builds and canonical runtime SBOMs",
        "Seal and verify release artifacts",
        "Package deterministic source snapshot twice",
        "Stage checksum-bound release bundle",
        "Verify staged release bundle",
        "Upload verified release bundle",
    }
    selected = [step for step in steps if step.get("name") in required_steps]
    assert {step["name"] for step in selected} == required_steps
    assert all("if" not in step for step in selected)


def test_reusable_release_build_never_reresolves_reviewed_project_graph() -> None:
    workflow, raw = _workflow()
    build = workflow["jobs"]["build"]
    assert "PYTHONPATH" not in build["env"]

    steps = build["steps"]
    runs = [str(step.get("run", "")) for step in steps if "run" in step]
    syncs = [run.strip() for run in runs if run.strip().startswith("uv sync ")]
    assert syncs == ["uv sync --locked --no-default-groups --no-install-project"]
    assert "--extra dev" not in raw
    assert "uv run " not in raw

    cli_lines = [
        line.strip()
        for run in runs
        for line in run.splitlines()
        if line.strip().startswith("PYTHONPATH=src .venv/bin/python -m e2h ")
    ]
    assert len(cli_lines) == 11

    sbom = next(
        step for step in steps if step.get("name") == "Export and canonicalize runtime SBOM twice"
    )
    sbom_run = str(sbom["run"])
    assert sbom_run.count("uv export --locked --no-default-groups") == 2


def test_reusable_release_build_verifies_toolchain_against_independent_source() -> None:
    workflow, _ = _workflow()
    steps = workflow["jobs"]["build"]["steps"]
    seal = next(step for step in steps if step.get("name") == "Seal and verify release artifacts")
    run = str(seal["run"])
    assert "e2h release seal dist-a" in run
    assert "--toolchain-root source-a" in run
    assert "e2h release verify-toolchain" in run
    assert "release-manifest.json source-b" in run
    assert '--expected-source-commit "$GITHUB_SHA"' in run
    assert "> release-toolchain-verification.json" in run
    assert 'len(evidence["source_tree_sha256"]) == 64' in run
    assert 'verification["source_inputs_verified"] is True' in run
    assert 'verification["source_tree_verified"] is True' in run
    assert 'verification["source_commit_verified"] is True' in run
    assert 'verification["evidence"] == evidence' in run


def test_reusable_release_build_packages_verified_source_snapshot_twice() -> None:
    workflow, _ = _workflow()
    steps = workflow["jobs"]["build"]["steps"]
    package = next(
        step for step in steps if step.get("name") == "Package deterministic source snapshot twice"
    )
    run = str(package["run"])
    assert "snapshot create" in run
    assert "source-a source-a.e2hsnap" in run
    assert "source-b source-b.e2hsnap" in run
    assert "cmp source-a.e2hsnap source-b.e2hsnap" in run
    assert "snapshot verify source-a.e2hsnap" in run
    assert "release-source-inspection.json" in run
    assert 'source["snapshot_id"] == source_tree' in run
    assert "mv source-a.e2hsnap e2h-source.e2hsnap" in run
    assert "rm source-b.e2hsnap" in run


def test_reusable_release_build_self_verifies_finalized_bundle_before_upload() -> None:
    workflow, _ = _workflow()
    steps = workflow["jobs"]["build"]["steps"]
    names = [step.get("name") for step in steps]
    verify_index = names.index("Verify staged release bundle")
    upload_index = names.index("Upload verified release bundle")
    assert verify_index < upload_index

    verify = steps[verify_index]
    run = str(verify["run"])
    assert "e2h release verify-bundle" in run
    assert "release \\" in run
    assert '--expected-source-commit "$GITHUB_SHA"' in run
    assert "> release-bundle-verification.json" in run
    assert 'proof["verified"] is True' in run
    assert 'proof["source_commit_verified"] is True' in run
    assert 'proof["artifact_count"] == 2' in run
    assert 'proof["checksum_count"] == 9' in run
    assert 'len(proof["source_tree_sha256"]) == 64' in run


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
    stage = next(
        step for step in steps if step.get("name") == "Stage checksum-bound release bundle"
    )
    run = str(stage["run"])
    assert "release/e2h-sbom.cdx.json" in run
    assert "cp e2h-source.e2hsnap release/" in run
    assert "e2h-source.e2hsnap" in run
    assert "release-manifest.json" in run
    assert "release-verification.json" in run
    assert "release-toolchain-verification.json" in run
    assert "release-inspection.json" in run
    assert "release-source-inspection.json" in run
    assert "release-checksums.txt" in run
