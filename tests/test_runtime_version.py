from __future__ import annotations

import tomllib
from pathlib import Path

from e2h._version import VERSION, runtime_user_agent

ROOT = Path(__file__).resolve().parents[1]


def test_internal_version_matches_project_and_lock_metadata() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    assert VERSION == "0.28.0"
    assert project_version == VERSION
    assert f'name = "e2h"\nversion = "{VERSION}"' in lock


def test_provider_runtime_user_agents_share_package_version() -> None:
    assert runtime_user_agent("openai") == "e2h-openai-runtime/0.28.0"
    assert runtime_user_agent("anthropic") == "e2h-anthropic-runtime/0.28.0"
    assert runtime_user_agent("gemini") == "e2h-gemini-runtime/0.28.0"
