from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "publish-pypi.yml"

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


def _workflow() -> tuple[dict[str, Any], str]:
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict)
    return parsed, raw


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


def test_publish_workflow_is_tag_only_and_non_canceling() -> None:
    workflow, _ = _workflow()
    trigger = _trigger(workflow)
    assert trigger == {"push": {"tags": ["v*.*.*"]}}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert workflow["permissions"] == {"contents": "read"}


def test_oidc_permissions_are_job_scoped_and_separated() -> None:
    workflow, _ = _workflow()
    jobs = workflow["jobs"]
    assert set(jobs) == {"build", "attest", "publish"}

    assert jobs["build"]["permissions"] == {"contents": "read"}
    assert "id-token" not in jobs["build"]["permissions"]

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


def test_oidc_jobs_never_checkout_or_execute_project_code() -> None:
    workflow, _ = _workflow()
    jobs = workflow["jobs"]
    for name in ("attest", "publish"):
        uses = _uses(jobs[name])
        assert not any(item.startswith("actions/checkout@") for item in uses)
        runs = [str(step.get("run", "")) for step in _steps(jobs[name])]
        assert not any("uv run" in run or "python " in run for run in runs)


def test_release_build_requires_version_match_main_ancestry_and_reproducibility() -> None:
    workflow, raw = _workflow()
    build_runs = "\n".join(str(step.get("run", "")) for step in _steps(workflow["jobs"]["build"]))
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in raw
    assert '"$GITHUB_REF_NAME" != "v$VERSION"' in build_runs
    assert "git merge-base --is-ancestor HEAD origin/main" in build_runs
    assert "git archive HEAD | tar -x -C source-a" in build_runs
    assert "git archive HEAD | tar -x -C source-b" in build_runs
    assert "assert first == second" in build_runs
    assert "e2h release seal" in build_runs
    assert "e2h release verify" in build_runs


def test_release_bundle_is_verified_before_attestation_and_publication() -> None:
    workflow, _ = _workflow()
    jobs = workflow["jobs"]
    assert jobs["attest"]["needs"] == "build"
    assert jobs["publish"]["needs"] == ["build", "attest"]

    for name in ("attest", "publish"):
        runs = "\n".join(str(step.get("run", "")) for step in _steps(jobs[name]))
        assert "sha256sum -c release-checksums.txt" in runs
        assert any(item.startswith("actions/download-artifact@") for item in _uses(jobs[name]))

    publish_steps = _steps(jobs["publish"])
    publish = next(step for step in publish_steps if str(step.get("uses", "")).startswith("pypa/"))
    assert publish["with"] == {
        "packages-dir": "release/dist/",
        "verify-metadata": True,
        "print-hash": True,
        "attestations": True,
    }


def test_every_third_party_action_is_immutable_sha_pinned() -> None:
    workflow, _ = _workflow()
    seen: set[str] = set()
    for job in workflow["jobs"].values():
        for use in _uses(job):
            assert _SHA_PIN.fullmatch(use), use
            seen.add(use)
    assert seen == EXPECTED_ACTIONS


def test_publish_workflow_contains_no_static_pypi_credentials() -> None:
    _, raw = _workflow()
    lowered = raw.lower()
    assert "secrets." not in lowered
    assert "pypi_token" not in lowered
    assert "password:" not in lowered
    assert "workflow_dispatch" not in lowered
    assert "pull_request_target" not in lowered
