from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE_INTEGRITY = ROOT / ".github" / "workflows" / "release-integrity.yml"
PUBLISH = ROOT / ".github" / "workflows" / "publish-pypi.yml"
EXPECTED_RUNNER = "ubuntu-24.04"
EXPECTED_EPOCH = "1704067200"


def _workflow(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _seal_command(path: Path) -> tuple[dict[str, Any], str]:
    workflow = _workflow(path)
    job_name = "reproducible-build" if path == RELEASE_INTEGRITY else "build"
    job = workflow["jobs"][job_name]
    steps = job["steps"]
    matching = [step for step in steps if "e2h release seal" in str(step.get("run", ""))]
    assert len(matching) == 1
    command = str(matching[0]["run"])
    return job, command


def test_release_builds_bind_manifest_to_same_workflow_toolchain_context() -> None:
    for path in (RELEASE_INTEGRITY, PUBLISH):
        job, command = _seal_command(path)
        assert job["runs-on"] == EXPECTED_RUNNER
        assert job["env"]["SOURCE_DATE_EPOCH"] == EXPECTED_EPOCH
        assert "--toolchain-root ." in command
        assert '--source-commit "$GITHUB_SHA"' in command
        assert f"--runner-generation {EXPECTED_RUNNER}" in command
        assert '--source-date-epoch "$SOURCE_DATE_EPOCH"' in command


def test_toolchain_evidence_stays_inside_manifest_checksum_chain() -> None:
    publish = _workflow(PUBLISH)
    build_runs = "\n".join(str(step.get("run", "")) for step in publish["jobs"]["build"]["steps"])
    assert "release-manifest.json" in build_runs
    assert "sha256sum dist/*.tar.gz dist/*.whl" in build_runs
    assert "release-manifest.json release-verification.json" in build_runs

    integrity = _workflow(RELEASE_INTEGRITY)
    steps = integrity["jobs"]["reproducible-build"]["steps"]
    upload = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    path = str(upload["with"]["path"])
    assert "release-manifest.json" in path
    assert "release-inspection.json" in path
