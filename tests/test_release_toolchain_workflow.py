from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
REUSABLE = ROOT / ".github" / "workflows" / "reusable-release-build.yml"
RELEASE_INTEGRITY = ROOT / ".github" / "workflows" / "release-integrity.yml"
PUBLISH = ROOT / ".github" / "workflows" / "publish-pypi.yml"
EXPECTED_RUNNER = "ubuntu-24.04"
EXPECTED_EPOCH = "1704067200"
LOCAL_REUSABLE = "./.github/workflows/reusable-release-build.yml"


def _workflow(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _seal_command() -> tuple[dict[str, Any], str]:
    workflow = _workflow(REUSABLE)
    job = workflow["jobs"]["build"]
    steps = job["steps"]
    matching = [step for step in steps if "e2h release seal" in str(step.get("run", ""))]
    assert len(matching) == 1
    command = str(matching[0]["run"])
    return job, command


def test_reusable_build_binds_manifest_to_reviewed_toolchain_context() -> None:
    job, command = _seal_command()
    assert job["runs-on"] == EXPECTED_RUNNER
    assert job["env"]["SOURCE_DATE_EPOCH"] == EXPECTED_EPOCH
    assert "--toolchain-root source-a" in command
    assert '--source-commit "$GITHUB_SHA"' in command
    assert f"--runner-generation {EXPECTED_RUNNER}" in command
    assert '--source-date-epoch "$SOURCE_DATE_EPOCH"' in command


def test_both_release_callers_use_same_local_build_with_expected_tag_policy() -> None:
    integrity = _workflow(RELEASE_INTEGRITY)["jobs"]["reproducible-build"]
    publish = _workflow(PUBLISH)["jobs"]["build"]
    for job in (integrity, publish):
        assert job["uses"] == LOCAL_REUSABLE
        assert job["permissions"] == {"contents": "read"}
    assert integrity["with"] == {
        "artifact-name": "release-integrity",
        "validate-release-tag": False,
    }
    assert publish["with"] == {
        "artifact-name": "pypi-release",
        "validate-release-tag": True,
    }


def test_toolchain_evidence_stays_inside_checksum_bound_bundle() -> None:
    workflow = _workflow(REUSABLE)
    steps = workflow["jobs"]["build"]["steps"]
    runs = "\n".join(str(step.get("run", "")) for step in steps)
    assert "release-manifest.json" in runs
    assert "release-inspection.json" in runs
    assert "sha256sum dist/*.tar.gz dist/*.whl" in runs
    assert "release-manifest.json release-verification.json release-inspection.json" in runs

    upload = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["with"]["path"] == "release/"
