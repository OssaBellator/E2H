from __future__ import annotations

import tomllib
from pathlib import Path

import e2h
from e2h import anthropic_runtime, gemini_runtime, runtime_plan

ROOT = Path(__file__).resolve().parents[1]


ANTHROPIC_RUNTIME_EXPORTS = {
    "AnthropicHTTPResult": anthropic_runtime.AnthropicHTTPResult,
    "AnthropicMessagesInvocation": anthropic_runtime.AnthropicMessagesInvocation,
    "AnthropicMessagesRequest": anthropic_runtime.AnthropicMessagesRequest,
    "AnthropicMessagesRuntimeResult": anthropic_runtime.AnthropicMessagesRuntimeResult,
    "AnthropicRuntimeError": anthropic_runtime.AnthropicRuntimeError,
    "build_anthropic_messages_request": anthropic_runtime.build_anthropic_messages_request,
    "load_anthropic_messages_invocation": anthropic_runtime.load_anthropic_messages_invocation,
    "run_anthropic_messages": anthropic_runtime.run_anthropic_messages,
}

GEMINI_RUNTIME_EXPORTS = {
    "GeminiGenerateContentInvocation": gemini_runtime.GeminiGenerateContentInvocation,
    "GeminiGenerateContentRequest": gemini_runtime.GeminiGenerateContentRequest,
    "GeminiGenerateContentRuntimeResult": gemini_runtime.GeminiGenerateContentRuntimeResult,
    "GeminiHTTPResult": gemini_runtime.GeminiHTTPResult,
    "GeminiRuntimeError": gemini_runtime.GeminiRuntimeError,
    "build_gemini_generate_content_request": gemini_runtime.build_gemini_generate_content_request,
    "load_gemini_generate_content_invocation": (
        gemini_runtime.load_gemini_generate_content_invocation
    ),
    "run_gemini_generate_content": gemini_runtime.run_gemini_generate_content,
}

RUNTIME_PLAN_EXPORTS = {
    "RuntimeInvocation": runtime_plan.RuntimeInvocation,
    "RuntimePlanError": runtime_plan.RuntimePlanError,
    "RuntimeProvider": runtime_plan.RuntimeProvider,
    "RuntimeRequest": runtime_plan.RuntimeRequest,
    "RuntimeRequestPlan": runtime_plan.RuntimeRequestPlan,
    "load_runtime_request_plan": runtime_plan.load_runtime_request_plan,
    "plan_runtime_request": runtime_plan.plan_runtime_request,
}


def test_provider_runtime_exports_are_available_from_package_root() -> None:
    expected = {
        **ANTHROPIC_RUNTIME_EXPORTS,
        **GEMINI_RUNTIME_EXPORTS,
        **RUNTIME_PLAN_EXPORTS,
    }
    for name, value in expected.items():
        assert name in e2h.__all__
        assert getattr(e2h, name) is value


def test_package_version_matches_project_metadata() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert e2h.__version__ == project["version"]
