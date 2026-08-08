from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

import e2h.runtime_plan_cli as plan_cli
from e2h.main_cli import app
from e2h.openai_runtime import OpenAIRuntimeError
from e2h.runtime_plan_cli import plan_app

runner = CliRunner()


class _PlannedRequest(BaseModel):
    invocation_id: str = "runtime-plan"
    model: str = "provider-test-model"
    route_target_id: str = "primary"
    request_sha256: str = "1" * 64
    body: dict[str, object] = {"input": "planned"}


def _input_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = tuple(tmp_path / name for name in ("capsule.json", "variant.json", "invocation.json"))
    for path in paths:
        path.write_text("{}\n", encoding="utf-8")
    return paths


def _stub_common_loaders(monkeypatch: pytest.MonkeyPatch) -> tuple[object, object]:
    capsule_document = object()
    variant_document = object()
    monkeypatch.setattr(plan_cli, "load_capsule", lambda path: capsule_document)
    monkeypatch.setattr(plan_cli, "load_variant_document", lambda path: variant_document)
    return capsule_document, variant_document


@pytest.mark.parametrize(
    ("command", "invocation_loader_name", "builder_name", "key_name"),
    [
        (
            "openai-responses",
            "load_openai_responses_invocation",
            "build_openai_responses_request",
            "OPENAI_API_KEY",
        ),
        (
            "anthropic-messages",
            "load_anthropic_messages_invocation",
            "build_anthropic_messages_request",
            "ANTHROPIC_API_KEY",
        ),
        (
            "gemini-generate-content",
            "load_gemini_generate_content_invocation",
            "build_gemini_generate_content_request",
            "GEMINI_API_KEY",
        ),
    ],
)
def test_plan_commands_emit_request_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    invocation_loader_name: str,
    builder_name: str,
    key_name: str,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    output = tmp_path / f"{command}.json"
    capsule_document, variant_document = _stub_common_loaders(monkeypatch)
    invocation_document = object()
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        plan_cli,
        invocation_loader_name,
        lambda path: invocation_document,
    )

    def fake_builder(
        actual_variant: object,
        actual_capsule: object,
        actual_invocation: object,
    ) -> _PlannedRequest:
        observed.update(
            variant=actual_variant,
            capsule=actual_capsule,
            invocation=actual_invocation,
        )
        return _PlannedRequest()

    monkeypatch.setattr(plan_cli, builder_name, fake_builder)

    result = runner.invoke(
        plan_app,
        [
            command,
            str(capsule),
            str(variant),
            str(invocation),
            "--output",
            str(output),
            "--json",
        ],
        env={key_name: ""},
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["request_sha256"] == "1" * 64
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(result.stdout)
    assert observed == {
        "variant": variant_document,
        "capsule": capsule_document,
        "invocation": invocation_document,
    }


def test_plan_human_output_summarizes_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    _stub_common_loaders(monkeypatch)
    monkeypatch.setattr(plan_cli, "load_openai_responses_invocation", lambda path: object())
    monkeypatch.setattr(
        plan_cli,
        "build_openai_responses_request",
        lambda *args: _PlannedRequest(),
    )

    result = runner.invoke(
        plan_app,
        ["openai-responses", str(capsule), str(variant), str(invocation)],
    )

    assert result.exit_code == 0, result.output
    assert "runtime-plan" in result.stdout
    assert "provider-test-model" in result.stdout
    assert "primary" in result.stdout
    assert "1111111111111111" in result.stdout


def test_plan_failure_exits_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    _stub_common_loaders(monkeypatch)

    def fail_invocation(path: Path) -> object:
        raise OpenAIRuntimeError("invalid invocation")

    monkeypatch.setattr(plan_cli, "load_openai_responses_invocation", fail_invocation)
    result = runner.invoke(
        plan_app,
        ["openai-responses", str(capsule), str(variant), str(invocation)],
    )

    assert result.exit_code == 2
    assert "Unable to plan OpenAI Responses request" in result.stderr
    assert "invalid invocation" in result.stderr


def test_main_cli_registers_runtime_plan_group() -> None:
    result = runner.invoke(app, ["runtime", "plan", "--help"])

    assert result.exit_code == 0, result.output
    assert "openai-responses" in result.stdout
    assert "anthropic-messages" in result.stdout
    assert "gemini-generate-content" in result.stdout
