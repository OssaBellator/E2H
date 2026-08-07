from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
UV_CONFIG = ROOT / "uv.toml"
WORKFLOW_DIR = ROOT / ".github" / "workflows"
SETUP_UV = "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9"
EXPECTED_UV_VERSION = "0.12.2"


def _workflows() -> list[tuple[Path, dict[str, Any]]]:
    parsed: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")]):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict), path
        parsed.append((path, document))
    return parsed


def test_uv_toolchain_is_exactly_pinned_at_repository_root() -> None:
    with UV_CONFIG.open("rb") as handle:
        config = tomllib.load(handle)
    assert config == {"required-version": f"=={EXPECTED_UV_VERSION}"}


def test_workflows_discover_central_uv_version_after_checkout() -> None:
    seen = 0
    for path, workflow in _workflows():
        jobs = workflow.get("jobs")
        assert isinstance(jobs, dict), path
        for job in jobs.values():
            assert isinstance(job, dict), path
            steps = job.get("steps", [])
            assert isinstance(steps, list), path
            checked_out = False
            for step in steps:
                assert isinstance(step, dict), path
                uses = step.get("uses")
                if isinstance(uses, str) and uses.startswith("actions/checkout@"):
                    checked_out = True
                    continue
                if not isinstance(uses, str) or not uses.startswith("astral-sh/setup-uv@"):
                    continue
                seen += 1
                assert checked_out, f"setup-uv runs before checkout in {path}"
                assert uses == SETUP_UV, f"unexpected setup-uv revision in {path}: {uses}"
                inputs = step.get("with", {})
                assert isinstance(inputs, dict), path
                assert "version" not in inputs, f"workflow overrides central uv pin in {path}"
                assert "version-file" not in inputs, f"workflow overrides central uv pin in {path}"

    assert seen >= 1


def test_uv_pin_is_not_a_range_or_latest_alias() -> None:
    text = UV_CONFIG.read_text(encoding="utf-8").strip()
    assert text == f'required-version = "=={EXPECTED_UV_VERSION}"'
    assert "latest" not in text
    assert ">" not in text
    assert "<" not in text
    assert "*" not in text
