from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "publish-pypi.yml"
RELEASE_INTEGRITY_PATH = ROOT / ".github" / "workflows" / "release-integrity.yml"
RELEASE_NOTES_PATH = ROOT / ".github" / "release.yml"

EXPECTED_ACTIONS = {
    "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
    "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9",
    "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d",
    "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}
_SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _load_yaml(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed, raw


def _workflow() -> tuple[dict[str, Any], str]:
    return _load_yaml(WORKFLOW_PATH)


def _trigger(workflow: dict[str, Any]) -> Any:
    # PyYAML's YAML 1.1 resolver treats the plain key `on` as boolean true.
    return workflow.get("on", workflow.get(True))


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job.get("steps")
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def _uses(job: dict[str, Any]) -> list[str]:
    return [str(step["uses"]) for step in _steps(job) if "uses" in step]


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in _steps(job))


def test_publish_workflow_is_tag_only_and_non_canceling() -> None:
    workflow, _ = _workflow()
    trigger = _trigger(workflow)
    assert trigger == {"push": {"tags": ["v*.*.*"]}}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["permissions"] == {"contents": "read"}


def test_permissions_are_job_scoped_and_oidc_is_separated() -> None:
    workflow, _ = _workflow()
    jobs = workflow["jobs"]
    assert set(jobs) == {"build", "attest", "release-draft", "publish", "release-publish"}

    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert jobs["release-draft"]["permissions"] == {
        "actions": "read",
        "contents": "write",
    }
    assert jobs["release-publish"]["permissions"] == {
        "actions": "read",
        "contents": "write",
    }
    assert jobs["attest"]["permissions"] == {
        "actions": "read",
        "artifact-metadata": "write",
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
    }
    assert jobs["publish"]["permissions"] == {
        "actions": "read",
        "contents": "read",
        "id-token": "write",
    }
    assert jobs["publish"]["environment"] == "pypi"
    for name in ("build", "release-draft", "release-publish"):
        assert "id-token" not in jobs[name]["permissions"]


def test_identity_and_release_jobs_never_checkout_repository_code() -> None:
    workflow, _ = _workflow()
    jobs = workflow["jobs"]
    for name in ("attest", "release-draft", "publish", "release-publish"):
        assert not any(item.startswith("actions/checkout@") for item in _uses(jobs[name]))

    for name in ("attest", "publish"):
        runs = _runs(jobs[name])
        assert "uv run" not in runs
        assert "python " not in runs


def test_release_build_requires_version_main_reproducibility_and_runtime_sbom() -> None:
    workflow, raw = _workflow()
    build_runs = _runs(workflow["jobs"]["build"])
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in raw
    assert '"$GITHUB_REF_NAME" != "v$VERSION"' in build_runs
    assert "git merge-base --is-ancestor HEAD origin/main" in build_runs
    assert "git archive HEAD | tar -x -C source-a" in build_runs
    assert "git archive HEAD | tar -x -C source-b" in build_runs
    assert "assert first == second" in build_runs
    assert "uv export --locked --format cyclonedx1.5" in build_runs
    assert "e2h release canonicalize-sbom sbom-a.raw.json" in build_runs
    assert "e2h release canonicalize-sbom sbom-b.raw.json" in build_runs
    assert "assert sbom_a == sbom_b" in build_runs
    assert 'payload["bomFormat"] == "CycloneDX"' in build_runs
    assert 'payload["specVersion"] == "1.5"' in build_runs
    assert '"serialNumber" not in payload' in build_runs
    assert '"timestamp" not in payload.get("metadata", {})' in build_runs
    assert '{"mypy", "pytest", "pytest-cov", "ruff"}' in build_runs
    assert "e2h release seal" in build_runs
    assert "e2h release verify" in build_runs
    assert "e2h-sbom.cdx.json" in build_runs


def test_release_bundle_is_verified_at_every_trust_handoff() -> None:
    workflow, _ = _workflow()
    jobs = workflow["jobs"]
    assert jobs["attest"]["needs"] == "build"
    assert jobs["release-draft"]["needs"] == ["build", "attest"]
    assert jobs["publish"]["needs"] == ["build", "attest", "release-draft"]
    assert jobs["release-publish"]["needs"] == ["build", "attest", "release-draft", "publish"]

    for name in ("attest", "release-draft", "publish", "release-publish"):
        assert "sha256sum -c release-checksums.txt" in _runs(jobs[name])
        assert any(item.startswith("actions/download-artifact@") for item in _uses(jobs[name]))

    publish_steps = _steps(jobs["publish"])
    publish = next(step for step in publish_steps if str(step.get("uses", "")).startswith("pypa/"))
    assert publish["with"] == {
        "packages-dir": "release/dist/",
        "verify-metadata": True,
        "print-hash": True,
        "attestations": True,
    }


def test_sbom_is_attested_against_exact_distributions() -> None:
    workflow, _ = _workflow()
    attest_steps = [
        step
        for step in _steps(workflow["jobs"]["attest"])
        if str(step.get("uses", "")).startswith("actions/attest@")
    ]
    assert len(attest_steps) == 2
    sbom = next(step for step in attest_steps if "sbom-path" in step.get("with", {}))
    assert sbom["with"]["sbom-path"] == "release/e2h-sbom.cdx.json"
    subject_path = str(sbom["with"]["subject-path"])
    assert "release/dist/*.tar.gz" in subject_path
    assert "release/dist/*.whl" in subject_path


def test_github_release_is_drafted_then_published_as_immutable_and_verified() -> None:
    workflow, _ = _workflow()
    draft_runs = _runs(workflow["jobs"]["release-draft"])
    assert "/immutable-releases" in draft_runs
    assert "X-GitHub-Api-Version: 2026-03-10" in draft_runs
    assert "gh release create" in draft_runs
    assert "--draft" in draft_runs
    assert "--generate-notes" in draft_runs
    assert "--verify-tag" in draft_runs
    assert "--fail-on-no-commits" in draft_runs
    assert "gh release upload" in draft_runs
    assert "--clobber" in draft_runs

    publish_runs = _runs(workflow["jobs"]["release-publish"])
    assert 'gh release edit "$GITHUB_REF_NAME" --draft=false --verify-tag' in publish_runs
    assert "--json isImmutable" in publish_runs
    assert '"true"' in publish_runs
    assert "gh release verify" in publish_runs
    assert "gh release verify-asset" in publish_runs
    assert "release-attestation.json" in publish_runs


def test_generated_release_notes_have_ordered_categories_and_catchall() -> None:
    release_notes, _ = _load_yaml(RELEASE_NOTES_PATH)
    changelog = release_notes["changelog"]
    assert changelog["exclude"]["labels"] == ["skip-changelog"]
    categories = changelog["categories"]
    assert [category["title"] for category in categories] == [
        "Security and release integrity",
        "Features and improvements",
        "Fixes",
        "Documentation",
        "Dependencies",
        "Other changes",
    ]
    assert categories[-1]["labels"] == ["*"]


def test_supply_chain_workflows_pin_every_third_party_action_to_full_sha() -> None:
    workflow, _ = _workflow()
    seen: set[str] = set()
    for job in workflow["jobs"].values():
        for use in _uses(job):
            assert _SHA_PIN.fullmatch(use), use
            seen.add(use)
    assert seen == EXPECTED_ACTIONS

    release_integrity, _ = _load_yaml(RELEASE_INTEGRITY_PATH)
    for job in release_integrity["jobs"].values():
        for use in _uses(job):
            assert _SHA_PIN.fullmatch(use), use


def test_publish_workflow_contains_no_static_pypi_credentials_or_manual_trigger() -> None:
    _, raw = _workflow()
    lowered = raw.lower()
    assert "secrets." not in lowered
    assert "pypi_token" not in lowered
    assert "password:" not in lowered
    assert "workflow_dispatch" not in lowered
    assert "pull_request_target" not in lowered
