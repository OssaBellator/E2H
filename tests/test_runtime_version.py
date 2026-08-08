from __future__ import annotations

import tomllib
from pathlib import Path

import e2h
import e2h.anthropic_runtime as anthropic
import e2h.gemini_runtime as gemini
import e2h.openai_runtime as openai
from e2h._version import VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_telemetry_uses_project_version() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project_version = tomllib.load(handle)["project"]["version"]

    assert project_version == VERSION
    assert e2h.__version__ == project_version
    assert f"e2h-openai-runtime/{project_version}" == openai._USER_AGENT
    assert f"e2h-anthropic-runtime/{project_version}" == anthropic._USER_AGENT
    assert f"e2h-gemini-runtime/{project_version}" == gemini._USER_AGENT
